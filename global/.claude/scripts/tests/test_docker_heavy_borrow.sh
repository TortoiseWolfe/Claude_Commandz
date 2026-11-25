#!/usr/bin/env bash
# Tests that a light job waiting behind a long light job may take the heavy slot while it is free and no heavy job waits (issue 152), and that
# heavy jobs keep their own order and get the slot back. 23 Nov 2026: one 50-minute light job held the light slot while four light jobs
# waited, the heavy slot sat free and about 9 GB of memory stayed available.
# A stub docker stands in for the engine; nothing here touches the real one. Usage: test_docker_heavy_borrow.sh [path/to/docker-heavy.sh]
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"

SCRIPT="${1:-$(cd "$(dirname "$0")/.." && pwd -P)/docker-heavy.sh}"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dh-borrow.XXXXXX")"
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
# start <home> <name> <sleep> <slot flag or -> [env...]: start one job in the background; its pid goes to PIDS and $ST/<name>.pid
start() {
  local home=$1 name=$2 sl=$3 flag=$4; shift 4
  [ "$flag" = - ] && flag=""
  env "$@" HOME="$home" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_SLEEP="$sl" \
    bash "$SCRIPT" $flag --tag "$name" -- docker run --rm img "$name" >"$ST/$name.out" 2>&1 &
  PIDS+=($!); echo $! >"$ST/$name.pid"
}
finish() { rc=0; wait "$(cat "$ST/$1.pid")" || rc=$?; }
wait_file() { local _; for _ in $(seq 1 100); do [ -e "$1" ] && return 0; sleep 0.1; done; return 1; }
ran() { grep -qx "$1" "$ST/order.log"; }
cd "$TMP"

# Case 1: the light slot is held by a long job and the heavy slot is free with nobody waiting for it. A second light job starts at once, on
# the heavy slot, without waiting for the long job to end. The long job L runs 8 s; B must have started within 3 s and finish well before L.
H1="$TMP/home1"; D1="$H1/.cache/docker-heavy"; mkdir -p "$H1"; : >"$ST/order.log"
start "$H1" L 8 --light
wait_file "$D1/light.lock.holder" || fail "L took the light slot"
t0=$(date +%s)
start "$H1" B 1 --light
for _ in $(seq 1 30); do ran B && break; sleep 0.1; done
ran B || fail "a second light job starts while the long light job runs and the heavy slot is free: $(cat "$ST/B.out")"; ok
[ $(( $(date +%s) - t0 )) -lt 4 ] || fail "the second light job started at once, not after the long one"; ok
grep -q 'light job' "$D1/lock.holder" 2>/dev/null || fail "the heavy slot's holder file says a light job holds it: $(cat "$D1/lock.holder" 2>/dev/null)"; ok
finish B; [ "$rc" = 0 ] || fail "B exits 0, got $rc: $(cat "$ST/B.out")"; ok
[ ! -s "$D1/lock.holder" ] || fail "the heavy slot's holder file is cleared when the light job on it ends"; ok
kill -0 "$(cat "$ST/L.pid")" 2>/dev/null || fail "the long job was still running when B ended"; ok
finish L; [ "$rc" = 0 ] || fail "L exits 0, got $rc: $(cat "$ST/L.out")"; ok

# Case 2: a heavy job is waiting, so a light job behind the long light job does not take the heavy slot. To make the heavy slot free with a
# heavy job still waiting for it, the heavy waiter W is paused (SIGSTOP, it and its child) while the heavy holder K ends; then B (light, behind
# L) must not start for 2.5 s although the slot is free; then W resumes and the order must be W then B.
H2="$TMP/home2"; D2="$H2/.cache/docker-heavy"; mkdir -p "$H2"; : >"$ST/order.log"
start "$H2" K 2 -
wait_file "$D2/lock.holder" || fail "K took the heavy slot"
start "$H2" W 1 -; wait_file "$D2/jobs/W.job" || fail "W joined the heavy queue"; sleep 0.5
wpid=$(cat "$ST/W.pid"); wkids=$(cat /proc/"$wpid"/task/*/children 2>/dev/null || true)
# shellcheck disable=SC2086  # one pid a word
kill -STOP "$wpid" $wkids
start "$H2" L 12 --light
wait_file "$D2/light.lock.holder" || fail "L took the light slot"
start "$H2" B 1 --light; wait_file "$D2/jobs/B.job" || fail "B joined the light queue"
finish K; [ "$rc" = 0 ] || fail "K exits 0, got $rc: $(cat "$ST/K.out")"; ok
sleep 2.5   # time enough for B to take the free heavy slot if it would
if ran B; then fail "a light job does not take the heavy slot while a heavy job waits for it"; fi; ok
# shellcheck disable=SC2086
kill -CONT "$wpid" $wkids
for n in W B; do finish "$n"; [ "$rc" = 0 ] || fail "job $n exits 0, got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
# L (the long light job) logs when it starts on its own slot, so it sits between K and W; what matters is W before B
[ "$order" = "K L W B " ] || fail "case 2 order K L W B (heavy W before light B), got: $order"; ok
kill "$(cat "$ST/L.pid")" 2>/dev/null || true

# Case 3: a heavy job that arrives while a light job is on the heavy slot gets the slot back when that job ends, and runs before a later heavy
# job. L holds the light slot (8 s); B1 borrows the heavy slot (3 s); heavy H arrives and waits; it must start within a second of B1 ending.
H3="$TMP/home3"; D3="$H3/.cache/docker-heavy"; mkdir -p "$H3"; : >"$ST/order.log"
start "$H3" L 8 --light
wait_file "$D3/light.lock.holder" || fail "L took the light slot"
start "$H3" B1 3 --light
for _ in $(seq 1 30); do ran B1 && break; sleep 0.1; done
ran B1 || fail "B1 borrows the free heavy slot: $(cat "$ST/B1.out")"; ok
start "$H3" H 1 -
wait_file "$D3/jobs/H.job" || fail "H joined the heavy queue"
sleep 0.5; ran H && fail "H does not start while the light job holds the heavy slot"; ok
finish B1; [ "$rc" = 0 ] || fail "B1 exits 0, got $rc: $(cat "$ST/B1.out")"; ok
for _ in $(seq 1 30); do ran H && break; sleep 0.1; done
ran H || fail "H starts once the light job leaves the heavy slot"; ok
finish H; [ "$rc" = 0 ] || fail "H exits 0, got $rc: $(cat "$ST/H.out")"; ok
finish L

# Case 4: first come first served among the light jobs is kept. L holds the light slot, B1 and B2 queue in that order; B1 takes the heavy slot
# and B2 waits for L or the heavy slot (it gets the heavy slot only when B1 has left it); the run order is L B1 B2.
H4="$TMP/home4"; D4="$H4/.cache/docker-heavy"; mkdir -p "$H4"; : >"$ST/order.log"
start "$H4" L 6 --light
wait_file "$D4/light.lock.holder" || fail "L took the light slot"
start "$H4" B1 2 --light; wait_file "$D4/jobs/B1.job" || fail "B1 joined"; sleep 0.3
start "$H4" B2 1 --light; wait_file "$D4/jobs/B2.job" || fail "B2 joined"
for n in L B1 B2; do finish "$n"; [ "$rc" = 0 ] || fail "job $n exits 0, got $rc: $(cat "$ST/$n.out")"; ok; done
order=$(tr '\n' ' ' <"$ST/order.log")
[ "$order" = "L B1 B2 " ] || fail "light jobs run in the order they queued (L B1 B2), got: $order"; ok

echo "ok - $checks checks passed"
