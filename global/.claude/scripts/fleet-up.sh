#!/usr/bin/env bash
# fleet-up.sh: the fleet's "what state of recovery, when" script. Idempotent; the same file runs on both machines and does only
# what this machine's own files say (machine name from ~/.config/afa/machine, never a literal).
#
#   fleet-up.sh --heartbeat   every minute, from a job that already runs every minute (peer_poll.sh on the tower, muse_poll.sh on
#                             NX-01): stamps ~/.claude/state/heartbeat (line 1 epoch seconds, line 2 local time), keeps
#                             ~/.claude/state/lanes/live.tsv (the lanes' tmux windows right now), and, only when an earlier run saw a
#                             battery (state/battery.present), checks the battery. A stamp older than 5 minutes is first copied to
#                             state/heartbeat.gap, so the gap is still known when this job runs before the sign-in run.
#   fleet-up.sh               at sign-in and every 15 minutes (Windows task, windows/AFA-TowerKeeper.xml). If the heartbeat is older than
#                             5 minutes, Windows has booted since the last run, or the "lanes" tmux session is gone while live.tsv held
#                             lanes, it writes ~/.claude/state/recovery/<date>-<time>.md: when the machine went down (last heartbeat),
#                             why (Windows events 41 with the stop code and power-button fields, 6008 previous shutdown, 1074 planned
#                             restart with who and why, and the battery or UPS when one is present), when Windows started, when the user
#                             signed in, then each thing it brings back with its time:
#                               - the tmux session "lanes" at the fixed size (lane.sh creates it with the first restarted lane)
#                               - each dead lane with a handoff.md, started fresh with lane.sh --restart (never --resume: a restart
#                                 reads prompt.md and handoff.md, so it does not carry the old context)
#                               - long jobs in ~/.config/afa/services.tsv (see examples/services.tsv.example)
#                             It adds one line to this machine's inbox (peer-inbox.md off NX-01, hub-inbox.md on NX-01) so /prep shows it.
#                             The record reaches the other machine only through the notes route, which here makes Gmail drafts and never
#                             sends, so it cannot run unattended; the other machine reads its peer's heartbeat instead (below).
#                             The peer's heartbeat is read only when ~/.config/afa/peers names a target for it (examples/peers.example).
#   Battery (Win32_Battery; a UPS shows up there): when one reports it is discharging, log it to state/ups.log, send each live lane
#   "write handoff.md now", and write a PAUSE file into each folder listed in ~/.config/afa/pause-dirs so graphics renders stop. When
#   power returns it removes only the PAUSE files it wrote. Shutting down is left to Windows' own low-battery setting. With no battery
#   on the machine this is silent, and it switches on by itself the first time a battery appears.
#
#   --dry-run (or FLEET_DRY=1): print what would happen; start, send and write nothing outside the record on stdout.
# Test hooks: POWERSHELL, TMUX_BIN, LANE_SH, FLEET_NOW (epoch seconds), and HOME (everything lives under it).
set -u
dry=${FLEET_DRY:-0}; mode=full
for a in "$@"; do
  case "$a" in
    --dry-run) dry=1 ;;
    --heartbeat) mode=heartbeat ;;
    --battery) mode=battery ;;
    *) echo "usage: fleet-up.sh [--heartbeat|--battery] [--dry-run]" >&2; exit 2 ;;
  esac
done

CONF="$HOME/.config/afa"
STATE="$HOME/.claude/state"
LANES="$STATE/lanes"
POWERSHELL=${POWERSHELL:-powershell.exe}
TMUX_BIN=${TMUX_BIN:-tmux}
LANE_SH=${LANE_SH:-$HOME/.claude/scripts/lane.sh}
SESSION=lanes
SIZE_X=200; SIZE_Y=50   # the fixed size lane.sh gives a new session (lane.sh new_lane_session)
now=${FLEET_NOW:-$(date +%s)}
machine=$(tr -d ' \r\n' <"$CONF/machine" 2>/dev/null); machine=${machine:-unknown}
mkdir -p "$STATE" "$LANES"

pretty() { date -d "@$1" '+%a %-d %b %-I:%M %p'; }
clock() { date -d "@$1" '+%-I:%M:%S %p'; }
iso_epoch() { date -d "$1" +%s 2>/dev/null; }   # a local "2026-10-28T14:05:11" -> epoch
atomic_write() { local f=$1 t; t=$(mktemp "$f.XXXXXX") && cat >"$t" && mv -f "$t" "$f"; }
log_ups() { if [ "$dry" = 1 ]; then echo "ups: $1"; return 0; fi; printf '%s\t%s\n' "$(date -d "@$now" '+%F %T')" "$1" >>"$STATE/ups.log"; }

# Names of long jobs in services.tsv (they are managed there, never restarted as lanes).
service_names() { [ -r "$CONF/services.tsv" ] && awk -F'\t' '!/^#/ && NF>=1 && $1!=""{print $1}' "$CONF/services.tsv"; }
is_service() { service_names | grep -qxF -- "$1"; }

lane_windows() {  # the lanes' tmux windows that are lanes (have an index row) and are not long jobs
  "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null || return 0
  "$TMUX_BIN" list-windows -t "$SESSION" -F '#W' 2>/dev/null | while read -r w; do
    [ -n "$w" ] || continue
    is_service "$w" && continue
    [ -s "$LANES/index.tsv" ] && awk -F'\t' -v n="$w" '$2==n{f=1} END{exit !f}' "$LANES/index.tsv" && echo "$w"
  done
}

# ---- Windows queries (one PowerShell call each; empty when PowerShell is not reachable) ----
ps_run() { timeout 90 "$POWERSHELL" -NoProfile -NonInteractive -Command "$1" 2>/dev/null | tr -d '\r'; }

battery_line() {  # BATT=<status>|<charge percent>|<name>, or nothing when the machine has no battery
  ps_run '$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1; if ($b) { "BATT=$($b.BatteryStatus)|$($b.EstimatedChargeRemaining)|$($b.Name)" }' | grep '^BATT=' | head -1
}

battery_check() {
  local line status charge name
  line=$(battery_line)
  if [ -z "$line" ]; then rm -f "$STATE/battery.present"; return 0; fi
  if [ ! -e "$STATE/battery.present" ]; then log_ups "battery found: ${line#BATT=} (battery watch is now on)"; fi
  [ "$dry" = 1 ] || printf '%s\n' "$line" | atomic_write "$STATE/battery.present"
  IFS='|' read -r status charge name <<<"${line#BATT=}"
  if [ "$status" = 1 ]; then   # 1 = discharging: running on battery
    if [ ! -e "$STATE/battery.discharging" ]; then
      log_ups "discharging at ${charge:-?}% ($name): asking live lanes for a hand-off, pausing renders"
      [ "$dry" = 1 ] || printf '%s\n' "$now" >"$STATE/battery.discharging"
      local w
      for w in $(lane_windows); do
        if [ "$dry" = 1 ]; then echo "would send $w: write handoff.md now"
        else "$LANE_SH" --send "$w" "Power is on battery. Finish only the step in hand and write handoff.md now." >>"$STATE/fleet-up.log" 2>&1 \
          && log_ups "sent hand-off request to $w" || log_ups "could not reach $w"; fi
      done
      if [ -r "$CONF/pause-dirs" ]; then
        local d
        while read -r d; do
          case "$d" in ''|'#'*) continue ;; esac
          d=${d/#\~/$HOME}
          [ -d "$d" ] || continue
          if [ "$dry" = 1 ]; then echo "would write $d/PAUSE"; continue; fi
          if [ ! -e "$d/PAUSE" ]; then
            printf 'paused by fleet-up.sh at %s: the machine is on battery\n' "$(pretty "$now")" >"$d/PAUSE" \
              && printf '%s\n' "$d/PAUSE" >>"$STATE/battery.discharging" && log_ups "paused renders in $d"
          fi
        done <"$CONF/pause-dirs"
      fi
    fi
  elif [ -e "$STATE/battery.discharging" ]; then
    log_ups "power is back (status $status, ${charge:-?}%): lifting the pauses this script wrote"
    if [ "$dry" != 1 ]; then
      tail -n +2 "$STATE/battery.discharging" | while read -r p; do [ -n "$p" ] && rm -f -- "$p"; done
      rm -f "$STATE/battery.discharging"
    fi
  fi
}

# ---- heartbeat (every minute) ----
if [ "$mode" = battery ]; then battery_check; exit 0; fi
if [ "$mode" = heartbeat ]; then
  if [ -r "$STATE/heartbeat" ]; then
    last=$(head -1 "$STATE/heartbeat")
    if [[ "$last" =~ ^[0-9]+$ ]] && ((now - last > 300)) && [ ! -e "$STATE/heartbeat.gap" ]; then
      cp "$STATE/heartbeat" "$STATE/heartbeat.gap"   # the stamp from before the gap; fleet-up.sh reads it at sign-in
    fi
  fi
  printf '%s\n%s\n' "$now" "$(date -d "@$now" -Is)" | atomic_write "$STATE/heartbeat"
  if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then lane_windows | atomic_write "$LANES/live.tsv"; fi
  [ -e "$STATE/battery.present" ] && battery_check
  exit 0
fi

# ---- full run ----
exec 8>"$STATE/fleet-up.lock"; flock -n 8 || exit 0
LOGF="$STATE/fleet-up.log"

hb_epoch=""; [ -r "$STATE/heartbeat" ] && hb_epoch=$(head -1 "$STATE/heartbeat")
gap_epoch=""; [ -r "$STATE/heartbeat.gap" ] && gap_epoch=$(head -1 "$STATE/heartbeat.gap")
down_epoch=""
if [[ "$gap_epoch" =~ ^[0-9]+$ ]]; then down_epoch=$gap_epoch
elif [[ "$hb_epoch" =~ ^[0-9]+$ ]] && ((now - hb_epoch > 300)); then down_epoch=$hb_epoch; fi
hours=24
[[ "$down_epoch" =~ ^[0-9]+$ ]] && hours=$(( (now - down_epoch) / 3600 + 2 ))

read -r -d '' PS_ALL <<'PSEOF'
$os = Get-CimInstance Win32_OperatingSystem
"BOOT=" + $os.LastBootUpTime.ToString("s")
$ex = Get-Process explorer -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 1
if ($ex) { "LOGON=" + $ex.StartTime.ToString("s") }
$since = (Get-Date).AddHours(-__HOURS__)
Get-WinEvent -FilterHashtable @{LogName="System"; Id=41,6008,1074; StartTime=$since} -ErrorAction SilentlyContinue | ForEach-Object {
  $e = $_
  if ($e.Id -eq 41) {
    $d = @{}; ([xml]$e.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_."#text" }
    "EVT=" + $e.TimeCreated.ToString("s") + "|41|stop code " + $d["BugcheckCode"] + ", power-button time " + $d["PowerButtonTimestamp"] + ", sleep in progress " + $d["SleepInProgress"]
  } elseif ($e.Id -eq 1074) {
    $p = $e.Properties
    "EVT=" + $e.TimeCreated.ToString("s") + "|1074|planned restart or shutdown by " + $p[6].Value + " with " + $p[0].Value + ": " + $p[4].Value + ", reason " + $p[2].Value + ", comment " + $p[5].Value
  } else {
    "EVT=" + $e.TimeCreated.ToString("s") + "|6008|" + (($e.Message -replace "\s+"," ").Trim())
  }
}
$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
if ($b) { "BATT=" + $b.BatteryStatus + "|" + $b.EstimatedChargeRemaining + "|" + $b.Name }
PSEOF
win=$(ps_run "${PS_ALL//__HOURS__/$hours}")

boot_iso=$(printf '%s\n' "$win" | sed -n 's/^BOOT=//p' | head -1)
logon_iso=$(printf '%s\n' "$win" | sed -n 's/^LOGON=//p' | head -1)
[ -n "$boot_iso" ] || boot_iso=$(uptime -s 2>/dev/null | tr ' ' 'T')   # fallback: when this Linux side started
prev_boot=""; [ -r "$STATE/fleet-up.boot" ] && prev_boot=$(cat "$STATE/fleet-up.boot")
booted=0; [ -n "$prev_boot" ] && [ -n "$boot_iso" ] && [ "$prev_boot" != "$boot_iso" ] && booted=1
lanes_gone=0
if [ -s "$LANES/live.tsv" ] && ! "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then lanes_gone=1; fi

battery_check   # keeps battery.present current, and acts when a battery is discharging

# The other machine's heartbeat, only when ~/.config/afa/peers names a target for it.
peer_note=""
if [ -r "$CONF/peers" ]; then
  while read -r pname ptarget _; do
    case "$pname" in ''|'#'*) continue ;; esac
    [ "$pname" = "$machine" ] && continue
    pe=$(timeout 15 ssh -o BatchMode=yes -o ConnectTimeout=5 "$ptarget" 'head -1 .claude/state/heartbeat' 2>/dev/null | tr -d '\r')
    if [[ "$pe" =~ ^[0-9]+$ ]]; then
      if ((now - pe > 600)); then peer_state="silent since $(pretty "$pe")"; else peer_state="alive"; fi
    else peer_state="not reachable"; fi
    peer_note="$pname heartbeat: $peer_state"
    prevp=""; [ -r "$STATE/peer-heartbeat.state" ] && prevp=$(cat "$STATE/peer-heartbeat.state")
    if [ "$peer_state" != "$prevp" ] && { [ "$peer_state" != alive ] || [ -n "$prevp" ]; }; then
      peer_changed="$pname: $peer_state"
    fi
    [ "$dry" = 1 ] || printf '%s' "$peer_state" >"$STATE/peer-heartbeat.state"
  done <"$CONF/peers"
fi

incident=0
{ [ -n "$down_epoch" ] || [ "$booted" = 1 ] || [ "$lanes_gone" = 1 ]; } && incident=1

remember() {   # keep what the next run compares against
  [ "$dry" = 1 ] && return 0
  [ -n "$boot_iso" ] && printf '%s' "$boot_iso" >"$STATE/fleet-up.boot"
  if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then lane_windows | atomic_write "$LANES/live.tsv"; fi
}

inbox_add() {  # <text>: one line in this machine's inbox
  local line inbox
  line=$(printf -- '- [ ] %s | %s' "$(date -d "@$now" '+%F %H:%M')" "${1:0:400}")
  if [ "$machine" = NX-01 ]; then inbox="$STATE/hub-inbox.md"; else inbox="$STATE/peer-inbox.md"; fi
  if [ "$dry" = 1 ]; then echo "would add to $inbox: $line"; return 0; fi
  ( flock -w 30 7 && printf '%s\n' "$line" >>"$inbox" ) 7>>"$STATE/hub-inbox.lock"
}

if [ "$incident" = 0 ]; then
  remember
  if [ -n "${peer_changed:-}" ]; then inbox_add "Fleet: $peer_changed (read over the home network by fleet-up.sh)"; fi
  exit 0
fi

# ---- something went down: bring it back and write the record ----
rec_dir="$STATE/recovery"
if [ "$dry" = 1 ]; then rec_dir=$(mktemp -d); trap 'rm -rf "$rec_dir"' EXIT; else mkdir -p "$rec_dir"; fi
rec="$rec_dir/$(date -d "@$now" '+%F-%H%M').md"
[ -e "$rec" ] && rec="$rec_dir/$(date -d "@$now" '+%F-%H%M%S').md"
back=()   # "time | what" lines, in the order they happened
note_back() { back+=("$(clock "${FLEET_NOW:-$(date +%s)}") | $1"); }

restart_lane() {  # <name>: start it fresh from its hand-off
  local name=$1 row dir model billing eff conn recyc ent pmcp hand args out
  row=$(awk -F'\t' -v n="$name" '$2==n{r=$0} END{if(r)print r}' "$LANES/index.tsv" 2>/dev/null)
  [ -n "$row" ] || { note_back "lane $name: not restarted, no index row"; return; }
  IFS=$'\t' read -r _ _ dir model billing _ _ _ _ eff conn recyc ent pmcp <<<"$row"
  hand="$LANES/$name"
  [ -s "$hand/handoff.md" ] || { note_back "lane $name: not restarted, no handoff.md in $hand"; return; }
  case "$billing" in console|subscription) ;; *) note_back "lane $name: not restarted, it ran on engine $billing (start it by hand)"; return ;; esac
  [ -d "$dir" ] || { note_back "lane $name: not restarted, folder $dir is gone"; return; }
  local note; note=$(mktemp "$rec_dir/.restart-note.XXXXXX")
  printf 'The machine restarted at %s after a power or Windows stop. Read your hand-off and carry on from the next step it names.\n' "$(pretty "$now")" >"$note"
  args=(--restart --claude-because "restart after the machine went down; same engine as before")
  [ -n "${eff:-}" ] && [ "$eff" != - ] && args+=(--effort "$eff")
  [ "${conn:-}" = off ] && args+=(--no-connectors) || args+=(--connectors)
  [ -n "${ent:-}" ] && [ "$ent" != - ] && args+=(--allow-high-tier "$ent")
  [ -n "${pmcp:-}" ] && [ "$pmcp" != - ] && args+=(--project-mcp "$pmcp")
  if [ "$dry" = 1 ]; then echo "would run: $LANE_SH ${args[*]} $name $dir $model $note"; rm -f "$note"; return; fi
  if out=$("$LANE_SH" "${args[@]}" "$name" "$dir" "$model" "$note" 2>&1); then
    note_back "lane $name restarted fresh from its hand-off (model $model)"
  else
    note_back "lane $name: lane.sh refused: $(printf '%s' "$out" | tail -1 | cut -c1-160)"
  fi
  printf '%s\n' "$out" >>"$LOGF"
  rm -f "$note"
}

dead=()
if [ -s "$LANES/live.tsv" ]; then
  have=" $(lane_windows | tr '\n' ' ')"
  while read -r l; do [ -n "$l" ] && [[ "$have" != *" $l "* ]] && dead+=("$l"); done <"$LANES/live.tsv"
fi
for l in "${dead[@]:-}"; do [ -n "$l" ] && restart_lane "$l"; done

if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  note_back "tmux session $SESSION is up ($(( ${#dead[@]} )) dead lane(s) found)"
elif [ "$dry" = 1 ]; then echo "would create tmux session $SESSION at ${SIZE_X}x${SIZE_Y}"
else
  "$TMUX_BIN" new-session -d -x "$SIZE_X" -y "$SIZE_Y" -s "$SESSION" -n fleet -c "$HOME" \
    && "$TMUX_BIN" set-option -t "$SESSION" window-size manual \
    && "$TMUX_BIN" set-option -t "$SESSION" default-size "${SIZE_X}x${SIZE_Y}" \
    && note_back "tmux session $SESSION created at ${SIZE_X}x${SIZE_Y}, window-size manual" \
    || note_back "tmux session $SESSION could not be created"
fi

if [ -r "$CONF/services.tsv" ]; then   # name, enabled (1 or 0), working folder, check command, start command
  while IFS=$'\t' read -r sname senabled sdir scheck sstart; do
    case "$sname" in ''|'#'*) continue ;; esac
    sdir=${sdir/#\~/$HOME}
    if [ -n "$scheck" ] && (cd "$sdir" 2>/dev/null && bash -c "$scheck" >/dev/null 2>&1); then note_back "service $sname: already running"; continue; fi
    if [ "$senabled" != 1 ]; then note_back "service $sname: down, left alone (disabled in services.tsv; it waits for Jonathan's word)"; continue; fi
    if [ "$dry" = 1 ]; then echo "would start service $sname in $sdir: $sstart"; continue; fi
    if (cd "$sdir" 2>&1 && bash -c "$sstart") >>"$LOGF" 2>&1; then note_back "service $sname started"; else note_back "service $sname: start command failed (see fleet-up.log)"; fi
  done <"$CONF/services.tsv"
fi

# ---- the record ----
why=""
while IFS= read -r l; do
  case "$l" in EVT=*) ev=${l#EVT=}; et=${ev%%|*}; rest=${ev#*|}; eid=${rest%%|*}; etx=${rest#*|}
    ee=$(iso_epoch "$et"); [ -n "$ee" ] && et=$(pretty "$ee")
    why+="- Windows event $eid at $et: $etx"$'\n' ;;
  esac
done <<<"$win"
[ -n "$why" ] || why=$'- No Windows stop event (41, 6008, 1074) in the look-back window; the cause is unknown (Windows may not have been reachable from here).\n'
battery_txt=$(printf '%s\n' "$win" | sed -n 's/^BATT=//p' | head -1)
{
  echo "# Recovery on $machine, $(pretty "$now")"
  echo
  if [ -n "$down_epoch" ]; then echo "- Went down: last heartbeat $(pretty "$down_epoch")."
  else echo "- Went down: the heartbeat was not stale; a boot or a missing lanes session was found instead."; fi
  if [ -n "$boot_iso" ]; then be=$(iso_epoch "$boot_iso"); echo "- Windows started: $( [ -n "$be" ] && pretty "$be" || echo "$boot_iso" )."; fi
  if [ -n "$logon_iso" ]; then le=$(iso_epoch "$logon_iso"); echo "- Signed in (desktop shell started): $( [ -n "$le" ] && pretty "$le" || echo "$logon_iso" )."
  else echo "- Signed in: no signed-in desktop was found yet."; fi
  if [ -n "$battery_txt" ]; then IFS='|' read -r bs bc bn <<<"$battery_txt"; echo "- Battery or UPS: $bn, status $bs, ${bc:-?}% charge (status 1 = discharging, 2 = on mains)."
  else echo "- Battery or UPS: none reported by Windows."; fi
  [ -n "$peer_note" ] && echo "- $peer_note."
  echo
  echo "## Why (Windows events)"
  printf '%b' "$why"
  echo
  echo "## Brought back"
  if [ "${#back[@]}" -eq 0 ]; then echo "- Nothing needed bringing back."; else for b in "${back[@]}"; do echo "- $b"; done; fi
} >"$rec.tmp"
if [ "$dry" = 1 ]; then cat "$rec.tmp"; rm -f "$rec.tmp"; exit 0; fi
mv -f "$rec.tmp" "$rec"
rm -f "$STATE/heartbeat.gap"
remember
if [ -n "$down_epoch" ]; then down_txt="down since $(pretty "$down_epoch")"; else down_txt="restart found"; fi
inbox_add "Recovery on $machine: $down_txt, ${#back[@]} thing(s) checked or brought back | record: $rec"
exit 0
