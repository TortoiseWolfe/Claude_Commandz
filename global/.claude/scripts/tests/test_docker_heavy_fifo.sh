#!/usr/bin/env bash
# Tests that docker-heavy.sh runs waiting jobs first come first served and never times a job out while the slot is busy with a live job.
# 29 Oct 2026: three robot lanes' Blender jobs were passed over until "gave up after an hour waiting for the slot" (exit 75): bare flock
# let a newcomer take the slot in the gap between two jobs, woke waiters in any order, and its hour counted time spent behind live jobs.
# A stub docker stands in for the engine; nothing here touches the real one. Usage: test_docker_heavy_fifo.sh [path/to/docker-heavy.sh]
set -euo pipefail

SCRIPT="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/docker-heavy.sh}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dh-fifo.XXXXXX")"
BIN="$TMP/bin"; ST="$TMP/st"
mkdir -p "$BIN" "$ST"
PIDS=()
cleanup_all() { local p; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; rm -rf "$TMP"; }
trap cleanup_all EXIT

cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
# fake docker: version answers; ps lists nothing; run logs its last argument to $FAKE_ST/order.log, then sleeps $FAKE_SLEEP seconds
case "$1" in
  version) echo 27.0.0 ;;
  ps) ;;
  run) for last; do :; done; echo "$last" >>"$FAKE_ST/order.log"; exec sleep "${FAKE_SLEEP:-1}" ;;
esac
STUB
chmod +x "$BIN/docker"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
# start <home> <name> <sleep> [env...]: start one job in the background; its pid goes to PIDS and $ST/<name>.pid
start() {
  local home=$1 name=$2 sl=$3; shift 3
  env "$@" HOME="$home" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_SLEEP="$sl" \
    bash "$SCRIPT" --tag "$name" -- docker run --rm img "$name" >"$ST/$name.out" 2>&1 &
  PIDS+=($!); echo $! >"$ST/$name.pid"
}
# finish <name>: wait for that job and set rc to its exit code (no command substitution: a subshell cannot wait on this shell's jobs)
finish() { rc=0; wait "$(cat "$ST/$1.pid")" || rc=$?; }
wait_file() { local _; for _ in $(seq 1 100); do [ -e "$1" ] && return 0; sleep 0.1; done; return 1; }
cd "$TMP"

# Case 1: first come first served. H holds the slot; A, B and C queue behind it in that order; D arrives last, just as H ends,
# which is when bare flock let a newcomer take the gap. The run order must be H A B C D, and every job must exit 0.
H1="$TMP/home1"; mkdir -p "$H1"; : >"$ST/order.log"
start "$H1" H 3
wait_file "$H1/.cache/docker-heavy/lock.holder" || fail "H took the slot"
for n in A B C; do sleep 0.3; start "$H1" "$n" 1; wait_file "$H1/.cache/docker-heavy/jobs/$n.job" || fail "$n joined the queue"; done
sleep 1.9   # H has about 0.2 s left
start "$H1" D 1
for n in H A B C D; do finish "$n"; [ "$rc" = 0 ] || fail "job $n exits 0, got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
[ "$order" = "H A B C D " ] || fail "jobs run in the order they queued (H A B C D), got: $order"; ok

# Case 2: no waiter times out while the slot is busy. DOCKER_HEAVY_WAIT is 2 s; the holder runs 6 s and two jobs wait behind it, the second
# for about 8 s in all. Both must run (exit 0), not give up with 75.
H2="$TMP/home2"; mkdir -p "$H2"; : >"$ST/order.log"
start "$H2" P 6 DOCKER_HEAVY_WAIT=2
wait_file "$H2/.cache/docker-heavy/lock.holder" || fail "P took the slot"
start "$H2" Q 2 DOCKER_HEAVY_WAIT=2; sleep 0.3
start "$H2" R 1 DOCKER_HEAVY_WAIT=2
for n in P Q R; do finish "$n"; [ "$rc" = 0 ] || fail "job $n waits behind a live holder without timing out (exit 0), got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
[ "$order" = "P Q R " ] || fail "case 2 order P Q R, got: $order"; ok

# Case 3: a slot that makes no progress still ends the wait. The lock is held by a plain flock (no live docker-heavy job, holder never
# changes) for longer than DOCKER_HEAVY_WAIT=2; the waiter must give up with exit 75 and say why.
H3="$TMP/home3"; mkdir -p "$H3/.cache/docker-heavy/jobs"
flock "$H3/.cache/docker-heavy/lock" sleep 15 & PIDS+=($!); stuck=$!
sleep 0.3
t0=$(date +%s)
start "$H3" W 1 DOCKER_HEAVY_WAIT=2
finish W
[ "$rc" = 75 ] || fail "a waiter behind a stuck lock gives up with 75, got $rc: $(cat "$ST/W.out")"; ok
grep -q 'made no progress for 2 s' "$ST/W.out" || fail "the give-up message says why: $(cat "$ST/W.out")"; ok
[ $(( $(date +%s) - t0 )) -lt 14 ] || fail "the stuck waiter gave up before the lock was released"; ok
kill "$stuck" 2>/dev/null || true; wait "$stuck" 2>/dev/null || true

# Case 4: a waiting job file written by the old script (no ticket in field 8, as the jobs queued before this change are) still counts as ahead,
# by its waiting-since second. X holds; Y waits and its file is cut back to the old seven fields; Z joins later and must run after Y.
H4="$TMP/home4"; J4="$H4/.cache/docker-heavy/jobs"; mkdir -p "$J4"; : >"$ST/order.log"
start "$H4" X 4
wait_file "$H4/.cache/docker-heavy/lock.holder" || fail "X took the slot"
t8=$(cut -f8 "$J4/X.job"); [[ "$t8" =~ ^[0-9]{18,}$ ]] || fail "the job file carries a nanosecond ticket in field 8, got '$t8'"; ok
start "$H4" Y 1
wait_file "$J4/Y.job" || fail "Y joined the queue"; sleep 0.2
cut -f1-7 "$J4/Y.job" >"$J4/Y.old" && mv "$J4/Y.old" "$J4/Y.job"
[ -z "$(cut -f8 "$J4/Y.job")" ] || fail "Y's file is in the old format"; ok
sleep 1.1   # Z joins in a later second than Y's waiting-since
start "$H4" Z 1
for n in X Y Z; do finish "$n"; [ "$rc" = 0 ] || fail "job $n exits 0, got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
[ "$order" = "X Y Z " ] || fail "an old-format waiting job keeps its place (X Y Z), got: $order"; ok

# Case 5: a newcomer never barges past a waiter. Bare flock let a new job take the slot in the gap between two jobs, before a waiter that
# had been queued for an hour got woken. To hit that gap every time, the waiter W is paused (SIGSTOP, it and its child) while the holder G
# ends and the newcomer N arrives; then W resumes. W queued first, so the order must be G W N.
H5="$TMP/home5"; J5="$H5/.cache/docker-heavy/jobs"; mkdir -p "$J5"; : >"$ST/order.log"
start "$H5" G 2
wait_file "$H5/.cache/docker-heavy/lock.holder" || fail "G took the slot"
start "$H5" W 1
wait_file "$J5/W.job" || fail "W joined the queue"; sleep 0.5
wpid=$(cat "$ST/W.pid"); wkids=$(cat /proc/"$wpid"/task/*/children 2>/dev/null || true)
# shellcheck disable=SC2086  # one pid a word
kill -STOP "$wpid" $wkids
finish G; [ "$rc" = 0 ] || fail "G exits 0, got $rc"
start "$H5" N 1
sleep 2.5   # time enough for N to take a free slot if it would
# shellcheck disable=SC2086
kill -CONT "$wpid" $wkids
for n in W N; do finish "$n"; [ "$rc" = 0 ] || fail "job $n exits 0, got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
[ "$order" = "G W N " ] || fail "a newcomer does not take the slot ahead of an older waiter (G W N), got: $order"; ok

echo "ok - $checks checks passed"
