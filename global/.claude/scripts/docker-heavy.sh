#!/usr/bin/env bash
# Run a heavy Docker job so it can't take the machine down.
#
#   docker-heavy.sh [--mem 12g] [--cpus 8] [--timeout 1800] [--tag NAME] -- docker run ...
#   docker-heavy.sh -- docker compose -p NAME run --rm SERVICE ...
#   docker-heavy.sh --light [--mem 4g] [--cpus 4] -- docker run ...   CPU-only jobs (background removal with rembg, image resizing)
#   docker-heavy.sh --list                  who holds each slot (tag, start time) and which jobs are waiting (tag, waiting since)
#   docker-heavy.sh --cancel TAG            kill the containers labelled docker-heavy=TAG and stop that job's waiting or running docker-heavy.sh
#                                           (exit 0 when something was stopped, 1 when nothing matched)
#
# Heavy = photogrammetry and scan solves, image matching over a site's photos, renders,
# Blender, big test matrices: anything that can use several GB or most of the cores.
#
# What it does:
# - Checks the Docker engine answers within 10 s first, so a dead engine fails fast instead of
#   freezing the session (2026-10-11: the engine died at 11:22 and every session hung on it).
# - One heavy job at a time across ALL sessions (flock on ~/.cache/docker-heavy/lock). Others wait
#   up to an hour and print who holds the lock.
# - For `docker run`: adds --memory/--memory-swap (no swap past the cap), --cpus and a low
#   --cpu-shares, so interactive work in other sessions keeps priority. For `docker compose run`,
#   limits must live in the compose file (mem_limit / cpus); the lock and timeout still apply.
# - --light is a second, separate slot (light.lock, defaults --cpus 4 --mem 4g): CPU-only jobs never wait behind the GPU job and never need
#   to bypass this script. Heavy jobs still queue one at a time among themselves, light jobs one at a time among themselves.
# - Runs at low CPU priority (nice 10) under a hard timeout (default 30 min). The timeout counts RUN time only: it starts after the slot is
#   taken. The slot wait comes first, up to 3600 s, so a caller must not wrap docker-heavy.sh in an outer timeout shorter than 3600 s plus
#   the run time (an outer timeout that fires first kills the wait or the run from outside, which is what the label cleanup below guards).
# - Every run is named by a tag (--tag NAME, else <pid>-<nanoseconds>) and writes a job file under ~/.cache/docker-heavy/jobs/ with its pid, so
#   --list and --cancel find it without pgrep -f or pkill -f: a process pattern also matches the claude lane whose prompt contains the words
#   (28 Oct 2026: a lane killed its own claude that way).
# - Stops its container however it ends. A container outlives its docker client: on 2026-10-11 an
#   outer `timeout` killed this script mid-run, its container ran on, and the slot's holder file
#   was gone while it did. `docker run` gets a label of its own (docker-heavy=<this run>), and on
#   exit whatever carries that label is killed. A label, not a name: nothing is parsed out of the
#   command (a `--name` after the image belongs to the container's own program), and no other
#   container can match it.
set -euo pipefail
DIR="$HOME/.cache/docker-heavy"   # own user-owned directory so lane.sh can --add-dir just this (Codex lanes cannot write ~/.cache)
JOBS="$DIR/jobs"
MEM="" CPUS="" TIMEOUT=1800 LIGHT=0 TAG="" ACTION=run CANCEL_TAG=""
usage() { echo "usage: docker-heavy.sh [--light] [--mem 12g] [--cpus 8] [--timeout 1800] [--tag NAME] -- docker ...  |  --list  |  --cancel TAG" >&2; exit 2; }
tag_ok() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]]; }
while [ $# -gt 0 ]; do
  case "$1" in
    --mem) [ $# -ge 2 ] || usage; MEM="$2"; shift 2 ;;
    --cpus) [ $# -ge 2 ] || usage; CPUS="$2"; shift 2 ;;
    --timeout) [ $# -ge 2 ] || usage; TIMEOUT="$2"; shift 2 ;;
    --light) LIGHT=1; shift ;;
    --tag) [ $# -ge 2 ] && tag_ok "$2" || usage; TAG="$2"; shift 2 ;;
    --list) ACTION=list; shift ;;
    --cancel) [ $# -ge 2 ] && tag_ok "$2" || usage; ACTION=cancel; CANCEL_TAG="$2"; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
if [ "$LIGHT" = 1 ]; then : "${MEM:=4g}" "${CPUS:=4}"; SLOT=light LOCK="$DIR/light.lock"; else : "${MEM:=12g}" "${CPUS:=8}"; SLOT=heavy LOCK="$DIR/lock"; fi

# job_field <file> <n>: field n of a job file (tab separated: pid, state, slot, since-epoch, waiting-since-epoch, command).
job_field() { cut -f"$2" "$1" 2>/dev/null; }
# job_alive <file>: the job file's pid is a live docker-heavy.sh (the pid is checked against /proc, so a reused pid is not mistaken for it).
job_alive() {
  local pid; pid=$(job_field "$1" 1)
  [[ "$pid" =~ ^[0-9]+$ ]] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'docker-heavy'
}

if [ "$ACTION" = list ]; then
  for slot in heavy light; do
    lock="$DIR/lock"; [ "$slot" = light ] && lock="$DIR/light.lock"
    if [ -s "$lock.holder" ]; then echo "$slot slot: held by $(cat "$lock.holder")"; else echo "$slot slot: free"; fi
  done
  for f in "$JOBS"/*.job; do
    [ -e "$f" ] || continue
    job_alive "$f" || { rm -f "$f"; continue; }
    [ "$(job_field "$f" 2)" = waiting ] || continue
    echo "waiting: $(basename "$f" .job) ($(job_field "$f" 3) slot) since $(date -d "@$(job_field "$f" 5)" '+%F %H:%M') pid $(job_field "$f" 1)"
  done
  exit 0
fi

if [ "$ACTION" = cancel ]; then
  stopped=0
  ids=$(timeout 10 docker ps -q --filter "label=docker-heavy=$CANCEL_TAG" 2>/dev/null || true)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086  # one id a word
    timeout 20 docker kill $ids >/dev/null 2>&1 || true
    echo "killed container(s): $(echo $ids)"; stopped=1
  fi
  f="$JOBS/$CANCEL_TAG.job"
  if [ -e "$f" ]; then
    if job_alive "$f"; then
      pid=$(job_field "$f" 1); kill -TERM "$pid" 2>/dev/null && { echo "stopped docker-heavy.sh pid $pid ($(job_field "$f" 2))"; stopped=1; }
    else rm -f "$f"; fi
  fi
  [ "$stopped" = 1 ] && exit 0
  echo "docker-heavy.sh: nothing matched tag $CANCEL_TAG" >&2; exit 1
fi

[ "${1:-}" = docker ] || { echo "docker-heavy.sh: the command after -- must start with docker" >&2; exit 2; }

if ! timeout 10 docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo "docker-heavy.sh: the Docker engine isn't answering. Not starting; start or restart Docker Desktop first." >&2
  exit 75
fi

cmd=("$@")
[ -n "$TAG" ] || TAG="$$-$(date +%s%N)"
if [ "${cmd[1]:-}" = run ]; then
  cmd=(docker run --label "docker-heavy=$TAG" --memory "$MEM" --memory-swap "$MEM" --cpus "$CPUS" --cpu-shares 256 "${cmd[@]:2}")
fi

mkdir -p "$JOBS"
JOB="$JOBS/$TAG.job"
[ ! -e "$JOB" ] || ! job_alive "$JOB" || { echo "docker-heavy.sh: a job tagged $TAG is already running or waiting" >&2; exit 2; }
now=$(date +%s)
printf '%s\twaiting\t%s\t%s\t%s\t%s\n' "$$" "$SLOT" "$now" "$now" "${*:1:6}" > "$JOB"
exec 9>>"$LOCK"
fpid=""
cleanup() {
  [ -z "$fpid" ] || kill "$fpid" 2>/dev/null || true
  ids=$(timeout 10 docker ps -q --filter "label=docker-heavy=$TAG" 2>/dev/null || true)
  if [ -n "$ids" ]; then
    # shellcheck disable=SC2086  # one id a word
    timeout 20 docker kill $ids >/dev/null 2>&1 || true
  fi
  # the holder file is ours only once we hold the slot (a waiting job must not remove the holder's)
  [ "$(job_field "$JOB" 2)" != running ] || rm -f "$LOCK.holder"
  rm -f "$JOB"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if ! flock -n 9; then
  echo "docker-heavy.sh: waiting for the $SLOT slot, held by: $(cat "$LOCK.holder" 2>/dev/null || echo unknown)" >&2
  # in the background and waited on, so --cancel (a TERM) ends the wait at once instead of after the child
  flock -w 3600 9 & fpid=$!
  wait "$fpid" || { echo "docker-heavy.sh: gave up after an hour waiting for the slot" >&2; fpid=""; exit 75; }
  fpid=""
fi
printf '%s\trunning\t%s\t%s\t%s\t%s\n' "$$" "$SLOT" "$(date +%s)" "$now" "${*:1:6}" > "$JOB"
printf 'tag %s since %s pid %s in %s: %s\n' "$TAG" "$(date '+%F %H:%M')" "$$" "$PWD" "${*:1:6}" > "$LOCK.holder"
# in the background and waited on, so a signal to this script runs cleanup at once (bash holds a
# trap until a foreground child ends); stdin passed on explicitly (a background job gets /dev/null).
# The timeout starts here, after the slot is taken: it counts run time only.
exec 3<&0
nice -n 10 timeout --kill-after=30 "$TIMEOUT" "${cmd[@]}" <&3 &
wait $!
