#!/usr/bin/env bash
# Keep the Harmonic Egg workstation CI runner up while the repo variable RUNNER_LABEL says CI runs here (21 Oct 2026, harmonic-egg#351).
#
# The runner has no systemd to restart it and dies with WSL, Docker Desktop and sleep. This rides the 15-minute Windows task
# (\AFA\OpenClawStatus -> openclaw_status_refresh.sh), whose `wsl.exe -d Debian` also brings WSL itself back after a restart.
# It does nothing at all unless RUNNER_LABEL=workstation, so undoing the fallback (unset the variable) switches this off by itself.
#
#   he_runner_watch.sh            start the runner if it is down; re-register it if GitHub deleted the registration
#   he_runner_watch.sh --check    print "up" or "down" and exit 0/1, change nothing (the Friday pre-release check)
set -u
repo=TortoiseWolfe/harmonic-egg
dir="$HOME/actions-runner-harmonic-egg"
state="$HOME/.claude/state/he-runner"
mkdir -p "$state"
log() { echo "$(date -u +%FT%TZ) $*" >> "$state/watch.log"; }

# An unset variable is a 404; no answer at all (network down) leaves $want empty too, and both mean do nothing.
want=$(timeout 30 gh api "repos/$repo/actions/variables/RUNNER_LABEL" --jq .value 2>/dev/null)
[ "$want" = workstation ] || { [ "${1:-}" = --check ] && echo "not in use (RUNNER_LABEL=${want:-unset})"; exit 0; }

online() { timeout 30 gh api "repos/$repo/actions/runners" --jq '.runners[]|select(.name=="workstation-wsl2")|.status' 2>/dev/null | grep -qx online; }

if [ "${1:-}" = --check ]; then
  if pgrep -x Runner.Listener >/dev/null && online; then echo up; exit 0; fi
  echo down; exit 1
fi

exec 9>"$state/lock"; flock -n 9 || exit 0
pgrep -x Runner.Listener >/dev/null && exit 0     # pgrep -x, never -f: -f matches this shell's own command line

cd "$dir" || { log "no runner directory $dir"; exit 1; }
if tail -5 runner-stdout.log 2>/dev/null | grep -q "registration has been deleted"; then
  # GitHub drops a runner that stayed offline for about 30 days. Register it again under the same name and label.
  log "registration deleted by GitHub; re-registering"
  ./config.sh remove --local >/dev/null 2>&1
  tok=$(timeout 30 gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token 2>/dev/null)
  [ -n "$tok" ] && ./config.sh --unattended --url "https://github.com/$repo" --token "$tok" --name workstation-wsl2 --labels workstation --replace >/dev/null 2>&1 \
    || { log "re-register failed"; echo "re-register failed" > "$state/alert"; exit 1; }
fi
log "runner was down; starting it"
setsid nohup ./run.sh >> runner-stdout.log 2>&1 < /dev/null &
sleep 20
if pgrep -x Runner.Listener >/dev/null; then log "runner up"; rm -f "$state/alert"; else log "runner failed to start"; echo "runner failed to start" > "$state/alert"; exit 1; fi
