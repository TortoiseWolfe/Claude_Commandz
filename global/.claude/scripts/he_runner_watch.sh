#!/usr/bin/env bash
# Keep the workstation CI runners up while a repo's variable RUNNER_LABEL says its CI runs here
# (21 Oct 2026, harmonic-egg#351; extended 22 Oct to Chattanooga-Digital and Eduity).
#
# The runners have no systemd to restart them and die with WSL, Docker Desktop and sleep. This rides the 15-minute Windows task
# (\AFA\OpenClawStatus -> openclaw_status_refresh.sh), whose `wsl.exe -d Debian` also brings WSL itself back after a restart.
# Per repo it does nothing at all unless that repo's RUNNER_LABEL=workstation, so undoing the fallback (unset the variable)
# switches the watch off by itself.
#
#   he_runner_watch.sh            start each runner that is down; re-register one GitHub deleted
#   he_runner_watch.sh --check    print "<repo>: up|down|not in use" per repo, exit 1 if any in-use runner is down; changes nothing
set -u
REPOS=(
  "TortoiseWolfe/harmonic-egg:$HOME/actions-runner-harmonic-egg"
  "TortoiseWolfe/Chattanooga-Digital:$HOME/actions-runner-chattanooga-digital"
  "TortoiseWolfe/Eduity:$HOME/actions-runner-eduity"
)
state="$HOME/.claude/state/he-runner"
mkdir -p "$state"
log() { echo "$(date -u +%FT%TZ) $*" >> "$state/watch.log"; }
check=0; [ "${1:-}" = --check ] && check=1

# The listener belonging to a runner directory (several listeners share one process name, so match on its working directory).
listener_up() {
  local p
  for p in $(pgrep -x Runner.Listener); do
    [ "$(readlink "/proc/$p/cwd" 2>/dev/null)" = "$1" ] && return 0
  done
  return 1
}

if [ $check = 0 ]; then exec 9>"$state/lock"; flock -n 9 || exit 0; fi
rc=0
for pair in "${REPOS[@]}"; do
  repo=${pair%%:*}; dir=${pair#*:}; short=${repo#*/}
  alert="$state/alert-$short"

  # An unset variable is a 404; no answer at all (network down) leaves $want empty too, and both mean do nothing.
  want=$(timeout 30 gh api "repos/$repo/actions/variables/RUNNER_LABEL" --jq .value 2>/dev/null) || want=
  if [ "$want" != workstation ]; then
    [ $check = 1 ] && echo "$short: not in use (RUNNER_LABEL=${want:-unset})"
    continue
  fi

  if [ $check = 1 ]; then
    online=$(timeout 30 gh api "repos/$repo/actions/runners" --jq '.runners[]|select(.name=="workstation-wsl2")|.status' 2>/dev/null)
    if listener_up "$dir" && [ "$online" = online ]; then echo "$short: up"; else echo "$short: down"; rc=1; fi
    continue
  fi

  listener_up "$dir" && continue
  cd "$dir" || { log "$short: no runner directory $dir"; rc=1; continue; }
  if tail -5 runner-stdout.log 2>/dev/null | grep -q "registration has been deleted"; then
    # GitHub drops a runner that stayed offline for about 30 days. Register it again under the same name and label.
    log "$short: registration deleted by GitHub; re-registering"
    ./config.sh remove --local >/dev/null 2>&1
    tok=$(timeout 30 gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token 2>/dev/null)
    if [ -z "$tok" ] || ! ./config.sh --unattended --url "https://github.com/$repo" --token "$tok" --name workstation-wsl2 --labels workstation --replace >/dev/null 2>&1; then
      log "$short: re-register failed"; echo "re-register failed" > "$alert"; rc=1; continue
    fi
  fi
  log "$short: runner was down; starting it"
  setsid nohup ./run.sh >> runner-stdout.log 2>&1 < /dev/null &
  sleep 20
  if listener_up "$dir"; then log "$short: runner up"; rm -f "$alert"; else log "$short: runner failed to start"; echo "runner failed to start" > "$alert"; rc=1; fi
done
exit $rc
