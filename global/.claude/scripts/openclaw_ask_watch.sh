#!/usr/bin/env bash
# openclaw_ask_watch.sh: run the ask poll the moment an item lands in the drop folder, instead of up to a minute later.
# Shell only, never a model. It looks at the folder twice a second, and when the set of files changes it waits a moment
# for the writer to finish, then runs openclaw_ask_poll.sh (the same pickup the scheduled task runs). It does nothing else.
#
#   openclaw_ask_watch.sh            watch (what the scheduled task runs)
#   openclaw_ask_watch.sh install    register \AFA\OpenClawAskWatch with Task Scheduler (idempotent, /F)
#
# Debounced: files that land while a poll runs, or inside OC_WATCH_MIN_GAP seconds of the last one, get ONE more run.
# A file the poll leaves behind (a rejected one) does not make it spin: it runs again only when the folder changes.
# Single instance: a flock on ~/.claude/state/openclaw-ask-watch.lock; a second copy exits at once. The poll has its own
# lock too, so the watcher and the 1-minute task never read the folder at the same time.
#
# Started the same way as \AFA\OpenClawAsk (conhost --headless wsl.exe -d Debian -- bash -lc <this script>), by a task that
# repeats every 5 minutes; while the watcher is alive the repeats are ignored, and if it ever dies the next repeat restarts it.
# \AFA\OpenClawAsk (every minute) stays as the fallback. Remove: schtasks.exe /Delete /TN '\AFA\OpenClawAskWatch' /F
set -u
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
stage=${OC_STAGE_DIR:-/mnt/wsl/ocstage}
poll=${OC_POLL_SCRIPT:-$here/openclaw_ask_poll.sh}
state=${OC_STATE_DIR:-$HOME/.claude/state}
tick=${OC_WATCH_TICK:-0.5}          # seconds between looks at the folder
settle=${OC_WATCH_SETTLE:-0.3}      # seconds to let a writer finish after a change
min_gap=${OC_WATCH_MIN_GAP:-2}      # least seconds between two polls

if [ "${1:-}" = install ]; then
  tmpw=$(wslpath "$(cmd.exe /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')") || exit 1
  xml="$tmpw/openclaw-ask-watch.xml"
  {
    printf '\xff\xfe'
    cat <<EOF | iconv -f UTF-8 -t UTF-16LE
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\AFA\\OpenClawAskWatch</URI></RegistrationInfo>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-10-16T19:30:00</StartBoundary>
      <Repetition><Interval>PT5M</Interval></Repetition>
    </TimeTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Windows\\System32\\conhost.exe</Command>
      <Arguments>--headless C:\\Windows\\System32\\wsl.exe -d Debian -- bash -lc $here/openclaw_ask_watch.sh</Arguments>
    </Exec>
  </Actions>
</Task>
EOF
  } > "$xml"
  schtasks.exe /Create /TN '\AFA\OpenClawAskWatch' /XML "$(wslpath -w "$xml")" /F
  rc=$?
  rm -f "$xml"
  exit "$rc"
fi

mkdir -p "$state"
exec 8>"$state/openclaw-ask-watch.lock"
flock -n 8 || exit 0

now_ms() { echo $(( $(date +%s%N) / 1000000 )); }
# The folder as the poll will see it: name, size, mtime of each plain file except a writer's temp file.
sig() { find "$stage" -maxdepth 1 -type f ! -name '.tmp-*' -printf '%f %s %T@\n' 2>/dev/null | sort; }

last=""
last_run=0
while :; do
  if [ ! -d "$stage" ]; then sleep 5; continue; fi
  cur=$(sig)
  if [ -z "$cur" ] || [ "$cur" = "$last" ]; then
    last=$cur
    sleep "$tick"
    continue
  fi
  wait_ms=$(( last_run + min_gap * 1000 - $(now_ms) ))
  [ "$wait_ms" -gt 0 ] && sleep "$(printf '%d.%03d' $((wait_ms / 1000)) $((wait_ms % 1000)))"
  sleep "$settle"
  last=$(sig)   # what this poll will see; anything that lands during it differs from this and gets the next run
  timeout 330 "$poll" >/dev/null 2>&1
  last_run=$(now_ms)
done
