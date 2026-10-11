#!/usr/bin/env bash
# Run a heavy Docker job so it can't take the machine down.
#
#   docker-heavy.sh [--mem 12g] [--cpus 8] [--timeout 1800] -- docker run ...
#   docker-heavy.sh -- docker compose -p NAME run --rm SERVICE ...
#
# Heavy = photogrammetry and scan solves, image matching over a site's photos, renders,
# Blender, big test matrices: anything that can use several GB or most of the cores.
#
# What it does:
# - Checks the Docker engine answers within 10 s first, so a dead engine fails fast instead of
#   freezing the session (2026-10-11: the engine died at 11:22 and every session hung on it).
# - One heavy job at a time across ALL sessions (flock on ~/.cache/docker-heavy.lock). Others wait
#   up to an hour and print who holds the lock.
# - For `docker run`: adds --memory/--memory-swap (no swap past the cap), --cpus and a low
#   --cpu-shares, so interactive work in other sessions keeps priority. For `docker compose run`,
#   limits must live in the compose file (mem_limit / cpus); the lock and timeout still apply.
# - Runs at low CPU priority (nice 10) under a hard timeout (default 30 min).
set -euo pipefail
MEM=12g CPUS=8 TIMEOUT=1800
while [ $# -gt 0 ]; do
  case "$1" in
    --mem) MEM="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "usage: docker-heavy.sh [--mem 12g] [--cpus 8] [--timeout 1800] -- docker ..." >&2; exit 2 ;;
  esac
done
[ "${1:-}" = docker ] || { echo "docker-heavy.sh: the command after -- must start with docker" >&2; exit 2; }

if ! timeout 10 docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo "docker-heavy.sh: the Docker engine isn't answering. Not starting; start or restart Docker Desktop first." >&2
  exit 75
fi

cmd=("$@")
if [ "${cmd[1]:-}" = run ]; then
  cmd=(docker run --memory "$MEM" --memory-swap "$MEM" --cpus "$CPUS" --cpu-shares 256 "${cmd[@]:2}")
fi

LOCK="$HOME/.cache/docker-heavy.lock"
mkdir -p "${LOCK%/*}"
exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "docker-heavy.sh: waiting for the heavy-job slot, held by: $(cat "$LOCK.holder" 2>/dev/null || echo unknown)" >&2
  flock -w 3600 9 || { echo "docker-heavy.sh: gave up after an hour waiting for the slot" >&2; exit 75; }
fi
printf '%s pid %s in %s: %s\n' "$(date '+%F %H:%M')" "$$" "$PWD" "${*:0:6}" > "$LOCK.holder"
trap 'rm -f "$LOCK.holder"' EXIT
nice -n 10 timeout --kill-after=30 "$TIMEOUT" "${cmd[@]}"
