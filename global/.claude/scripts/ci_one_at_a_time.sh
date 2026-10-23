#!/usr/bin/env bash
# Job-started / job-completed hook for the three workstation runners (harmonic-egg, Chattanooga-Digital, Eduity):
# only one job at a time runs on the shared Docker engine and the one workstation. Called by the runner's Runner.Worker,
# so $PPID is that worker. Usage: ci_one_at_a_time.sh start|done
set -u
dir="$HOME/.claude/state/he-runner"; lock="$dir/job-lock"; mkdir -p "$dir"
me=$PPID
# Leftovers of a crashed run that break the next one: gitleaks-action downloads to a fixed <tmpdir>/gitleaks.tmp and its
# installer refuses to overwrite it ("Destination file path /tmp/gitleaks.tmp already exists", Eduity, 22 Oct 2026).
# Only called once this job holds the lock, so no other runner on this host has a live job using it.
# (Checked 23 Oct: actionlint uses RUNNER_TEMP and shellcheck uses github.action_path, both per-runner; yamllint is a container.)
clean_stale_tmp() { rm -f "${TMPDIR:-/tmp}/gitleaks.tmp"; }
holder_alive() { local p; p=$(cat "$lock" 2>/dev/null) || return 1; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
case "${1:-}" in
  start)
    # Wait up to 40 minutes for the other runner's job, then run anyway rather than hang the job for good.
    for _ in $(seq 1 480); do
      if ( set -o noclobber; echo "$me" > "$lock" ) 2>/dev/null; then clean_stale_tmp; exit 0; fi
      holder_alive || rm -f "$lock"          # stale: its worker is gone
      sleep 5
    done
    echo "ci_one_at_a_time: waited 40 minutes for $(cat "$lock" 2>/dev/null); running anyway" >&2
    exit 0 ;;
  done)
    [ "$(cat "$lock" 2>/dev/null)" = "$me" ] && rm -f "$lock"
    exit 0 ;;
esac
