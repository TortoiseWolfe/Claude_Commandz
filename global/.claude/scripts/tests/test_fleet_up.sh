#!/usr/bin/env bash
# Tests for fleet-up.sh with a fake HOME and stub powershell.exe, tmux and lane.sh. Run: bash tests/test_fleet_up.sh
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
bad() { fail=$((fail + 1)); echo "FAIL $1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }

mkdir -p "$T/bin"
cat >"$T/bin/ps" <<'SH'
#!/usr/bin/env bash
# stub powershell.exe: prints the canned output; "Win32_Battery" only (battery mode) prints just the BATT line
if [[ "$*" != *Win32_OperatingSystem* ]]; then grep '^BATT=' "$FAKE_WIN" 2>/dev/null; else cat "$FAKE_WIN"; fi
SH
cat >"$T/bin/tmux" <<'SH'
#!/usr/bin/env bash
echo "tmux $*" >>"$FAKE_DIR/tmux.log"
case "$1" in
  has-session) [ -e "$FAKE_DIR/session" ] ;;
  list-windows) cat "$FAKE_DIR/windows" 2>/dev/null ;;
  new-session) touch "$FAKE_DIR/session" ;;
  *) : ;;
esac
SH
cat >"$T/bin/lane.sh" <<'SH'
#!/usr/bin/env bash
echo "lane.sh $*" >>"$FAKE_DIR/lane.log"
[ "${FAKE_LANE_FAIL:-}" = "$(printf '%s\n' "$@" | grep -m1 '^2026' )" ] && { echo "refusing: client repo" >&2; exit 1; }
exit 0
SH
chmod +x "$T/bin/"*

setup() {  # <machine>
  export HOME="$T/home-$1-$RANDOM"; export FAKE_DIR="$T/fake-$RANDOM"; export FAKE_WIN="$FAKE_DIR/win"
  mkdir -p "$HOME/.config/afa" "$HOME/.claude/state/lanes" "$FAKE_DIR" "$HOME/work"
  echo "$1" >"$HOME/.config/afa/machine"
  export POWERSHELL="$T/bin/ps" TMUX_BIN="$T/bin/tmux" LANE_SH="$T/bin/lane.sh"
  S=$HOME/.claude/state
}
run() { FLEET_NOW=$NOW bash "$HERE/fleet-up.sh" "$@"; }
NOW=1793200000
addlane() {  # <name> [with-handoff]
  printf '%s\t%s\t%s\t%s\tsubscription\tp\tr\tsid\t1\t-\ton\t-\t-\t-\n' "2026-10-28" "$1" "$HOME/work" sonnet >>"$S/lanes/index.tsv"
  [ "${2:-}" = handoff ] && { mkdir -p "$S/lanes/$1"; echo "next step" >"$S/lanes/$1/handoff.md"; }
}

# 1. heartbeat: stamp, live.tsv, gap copy
setup NCC-74656
addlane 2026-10-28-alpha handoff; touch "$FAKE_DIR/session"; echo 2026-10-28-alpha >"$FAKE_DIR/windows"
run --heartbeat
check "heartbeat stamps epoch" '[ "$(head -1 $S/heartbeat)" = $NOW ]'
check "heartbeat keeps live.tsv" 'grep -qx 2026-10-28-alpha $S/lanes/live.tsv'
check "no gap file for a fresh stamp" '[ ! -e $S/heartbeat.gap ]'
NOW=$((NOW + 1200)) run --heartbeat
check "stale stamp is copied to heartbeat.gap" '[ "$(head -1 $S/heartbeat.gap)" = 1793200000 ]'
check "heartbeat is the new time" '[ "$(head -1 $S/heartbeat)" = 1793201200 ]'
NOW=$((NOW + 1260)) run --heartbeat
check "a second gap does not overwrite the first gap" '[ "$(head -1 $S/heartbeat.gap)" = 1793200000 ]'

# 2. quiet machine: a fresh heartbeat and an unchanged boot do nothing
setup NCC-74656
printf '%s\n' "BOOT=2026-10-28T08:00:00" >"$FAKE_WIN"
run --heartbeat
NOW=$((NOW + 60)) run
check "first run stores the boot stamp" '[ "$(cat $S/fleet-up.boot)" = 2026-10-28T08:00:00 ]'
check "no incident, no record" '[ ! -d $S/recovery ] || [ -z "$(ls $S/recovery)" ]'
check "no incident, no inbox line" '[ ! -e $S/peer-inbox.md ]'

# 3. a crash: gap, dead lanes, services, record, inbox, idempotence
setup NCC-74656
addlane 2026-10-28-alpha handoff; addlane 2026-10-28-beta; addlane 2026-10-28-cli handoff
printf '2026-10-28-alpha\n2026-10-28-beta\n2026-10-28-cli\n' >"$S/lanes/live.tsv"
printf '%s\n' "$((NOW - 3000))" >"$S/heartbeat.gap"; printf '%s\n' "$((NOW - 30))" >"$S/heartbeat"
printf '%s\n' "2026-10-28T08:00:00" >"$S/fleet-up.boot"
cat >"$FAKE_WIN" <<'W'
BOOT=2026-10-28T14:30:00
LOGON=2026-10-28T14:33:00
EVT=2026-10-28T14:30:20|41|stop code 0, power-button time 0, sleep in progress 0
EVT=2026-10-28T14:30:25|6008|The previous system shutdown at 2:05:00 PM was unexpected.
EVT=2026-10-28T13:00:00|1074|planned restart or shutdown by someone with Windows Update: restart, reason Operating System: Upgrade, comment
W
printf 'bandwagon-live\t0\t%s\ttrue\techo no\n' "$HOME/work" >"$HOME/.config/afa/services.tsv"
printf 'renderer\t1\t%s\tfalse\ttouch %s/renderer-started\n' "$HOME/work" "$HOME/work" >>"$HOME/.config/afa/services.tsv"
FAKE_LANE_FAIL=2026-10-28-cli run
rec=$(ls $S/recovery/*.md 2>/dev/null | head -1)
check "a record file exists" '[ -n "$rec" ]'
check "record says when it went down" 'grep -q "Went down: last heartbeat" "$rec"'
check "record has event 41 with stop code and power-button fields" 'grep -q "event 41.*stop code 0, power-button time 0" "$rec"'
check "record has event 6008" 'grep -q "event 6008" "$rec"'
check "record has event 1074 with who and why" 'grep -q "event 1074.*restart or shutdown by someone.*reason Operating System" "$rec"'
check "record has Windows start and sign-in" 'grep -q "Windows started" "$rec" && grep -q "Signed in" "$rec"'
check "record says no battery" 'grep -q "Battery or UPS: none" "$rec"'
check "alpha restarted fresh with --restart" 'grep -q -- "--restart.* 2026-10-28-alpha $HOME/work sonnet" $FAKE_DIR/lane.log'
check "restart never uses --resume" '! grep -q -- "--resume" $FAKE_DIR/lane.log'
check "lane with no handoff.md is not restarted" '! grep -q "2026-10-28-beta" $FAKE_DIR/lane.log && grep -q "beta: not restarted, no handoff.md" "$rec"'
check "a refusal from lane.sh lands in the record" 'grep -q "cli: lane.sh refused" "$rec"'
check "tmux session was created at the fixed size" 'grep -q "new-session -d -x 200 -y 50 -s lanes" $FAKE_DIR/tmux.log && grep -q "window-size manual" $FAKE_DIR/tmux.log'
check "disabled service is left alone" 'grep -q "service bandwagon-live: already running\|service bandwagon-live: down, left alone" "$rec"'
check "enabled service is started" '[ -e $HOME/work/renderer-started ] && grep -q "service renderer started" "$rec"'
check "gap file removed" '[ ! -e $S/heartbeat.gap ]'
check "boot stamp updated" '[ "$(cat $S/fleet-up.boot)" = 2026-10-28T14:30:00 ]'
check "one inbox line on the tower" '[ "$(grep -c "^- \[ \] .*Recovery on NCC-74656" $S/peer-inbox.md)" = 1 ]'
check "inbox line has no hub-inbox on the tower" '[ ! -e $S/hub-inbox.md ]'
n=$(ls $S/recovery | wc -l); : >"$FAKE_DIR/lane.log"
NOW=$((NOW + 30)) run
check "second run is idempotent (no new record, no new lane starts)" '[ "$(ls $S/recovery | wc -l)" = "$n" ] && [ ! -s $FAKE_DIR/lane.log ]'

# 3b. disabled service that is down: left alone, named in the record
setup NCC-74656
printf '%s\n' "$((NOW - 3000))" >"$S/heartbeat.gap"; printf '%s\n' "$NOW" >"$S/heartbeat"; touch "$FAKE_DIR/session"
printf 'bandwagon-live\t0\t%s\tfalse\ttouch %s/stream-started\n' "$HOME/work" "$HOME/work" >"$HOME/.config/afa/services.tsv"
printf '%s\n' "BOOT=2026-10-28T14:30:00" >"$FAKE_WIN"
run
rec=$(ls $S/recovery/*.md | head -1)
check "disabled stream stays off and the record says it waits for Jonathan" '[ ! -e $HOME/work/stream-started ] && grep -q "bandwagon-live: down, left alone (disabled" "$rec"'
check "no Windows events: the record says the cause is unknown" 'grep -q "cause is unknown" "$rec"'

# 4. a boot with no gap still records
setup NX-01
printf '%s\n' "$NOW" >"$S/heartbeat"; printf '%s\n' "2026-10-27T08:00:00" >"$S/fleet-up.boot"; touch "$FAKE_DIR/session"
printf '%s\n' "BOOT=2026-10-28T09:00:00" >"$FAKE_WIN"
run
check "boot change alone writes a record" '[ -n "$(ls $S/recovery 2>/dev/null)" ]'
check "NX-01 writes hub-inbox.md, not peer-inbox.md" 'grep -q "Recovery on NX-01" $S/hub-inbox.md && [ ! -e $S/peer-inbox.md ]'

# 5. battery: silent without one, then switches on by itself, pauses, then lifts
setup NCC-74656
addlane 2026-10-28-alpha handoff; touch "$FAKE_DIR/session"; echo 2026-10-28-alpha >"$FAKE_DIR/windows"
mkdir -p "$HOME/render"; printf '# pause list\n%s\n' "$HOME/render" >"$HOME/.config/afa/pause-dirs"
printf '%s\n' "BOOT=2026-10-28T08:00:00" >"$FAKE_WIN"
run --heartbeat; run --heartbeat
check "no battery: no ups.log, no battery.present" '[ ! -e $S/ups.log ] && [ ! -e $S/battery.present ]'
NOW=$((NOW + 60)) run     # the 15-minute run finds no battery either
printf '%s\n' "BOOT=2026-10-28T08:00:00" "BATT=2|100|UPS Battery" >"$FAKE_WIN"
NOW=$((NOW + 60)) run
check "a battery appearing is logged and switches the watch on" 'grep -q "battery found" $S/ups.log && [ -e $S/battery.present ]'
check "on mains nothing is paused" '[ ! -e $HOME/render/PAUSE ]'
printf '%s\n' "BOOT=2026-10-28T08:00:00" "BATT=1|80|UPS Battery" >"$FAKE_WIN"
: >"$FAKE_DIR/lane.log"
NOW=$((NOW + 60)) run --heartbeat
check "discharging: live lane asked for a hand-off" 'grep -q -- "--send 2026-10-28-alpha .*write handoff.md now" $FAKE_DIR/lane.log'
check "discharging: PAUSE written and logged" '[ -e $HOME/render/PAUSE ] && grep -q "discharging at 80%" $S/ups.log'
: >"$FAKE_DIR/lane.log"
NOW=$((NOW + 60)) run --heartbeat
check "still discharging: lane is not asked twice" '[ ! -s $FAKE_DIR/lane.log ]'
printf '%s\n' "BOOT=2026-10-28T08:00:00" "BATT=2|85|UPS Battery" >"$FAKE_WIN"
NOW=$((NOW + 60)) run --heartbeat
check "power back: only our PAUSE is lifted, and it is logged" '[ ! -e $HOME/render/PAUSE ] && grep -q "power is back" $S/ups.log'
mkdir -p "$HOME/render2"; echo mine >"$HOME/render2/PAUSE"; echo "$HOME/render2" >>"$HOME/.config/afa/pause-dirs"
printf '%s\n' "BOOT=2026-10-28T08:00:00" "BATT=1|70|UPS Battery" >"$FAKE_WIN"
NOW=$((NOW + 60)) run --heartbeat
printf '%s\n' "BOOT=2026-10-28T08:00:00" "BATT=2|75|UPS Battery" >"$FAKE_WIN"
NOW=$((NOW + 60)) run --heartbeat
check "a PAUSE someone else wrote is left in place" '[ "$(cat $HOME/render2/PAUSE)" = mine ]'

# 6. dry run writes nothing
setup NCC-74656
printf '%s\n' "$((NOW - 3000))" >"$S/heartbeat.gap"; printf '%s\n' "$NOW" >"$S/heartbeat"
printf '%s\n' "BOOT=2026-10-28T14:30:00" >"$FAKE_WIN"
out=$(run --dry-run)
check "dry run prints the record and writes none" 'echo "$out" | grep -q "# Recovery on NCC-74656" && [ ! -d $S/recovery ] && [ -e $S/heartbeat.gap ]'

# 7. bad flag
bash "$HERE/fleet-up.sh" --nope >/dev/null 2>&1; check "unknown flag exits 2" '[ $? = 2 ]'

echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
