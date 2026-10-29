#!/usr/bin/env bash
# Tests for docker-heavy.sh --list, --cancel and --light (28 Oct 2026: a lane killed its own claude with pgrep -f while stopping a job; this
# script now finds a job by tag and pid file). A stub docker stands in for the engine; nothing here touches the real one.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/docker-heavy.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/dh-cancel.XXXXXX")"
BIN="$TMP/bin"; HOME_T="$TMP/home"; ST="$TMP/st"
mkdir -p "$BIN" "$HOME_T" "$ST"
cleanup_all() { for f in "$ST"/run-*.pid; do [ -e "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; done; rm -rf "$TMP"; }
trap cleanup_all EXIT

cat >"$BIN/docker" <<'STUB'
#!/usr/bin/env bash
# fake docker: version answers; run sleeps (and logs its arguments); ps -q --filter label=docker-heavy=<tag> lists the ids in $FAKE_ST/ids-<tag>; kill logs
case "$1" in
  version) echo 27.0.0 ;;
  run) printf '%s\n' "$*" >>"$FAKE_ST/run.log"; echo $$ >"$FAKE_ST/run-$$.pid"; exec sleep 25 ;;
  ps) for a; do case "$a" in label=docker-heavy=*) t=${a#label=docker-heavy=}; cat "$FAKE_ST/ids-$t" 2>/dev/null || true ;; esac; done ;;
  kill) shift; printf '%s\n' "$*" >>"$FAKE_ST/kill.log" ;;
esac
STUB
chmod +x "$BIN/docker"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
dh() { HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_ST="$ST" bash "$SCRIPT" "$@"; }
dh_rc() { set +e; LAST_OUT="$(dh "$@" 2>&1)"; LAST_RC=$?; set -e; }
wait_for() { local i; for i in $(seq 1 100); do [ -e "$1" ] && return 0; sleep 0.1; done; return 1; }

# The script text never uses a process-name pattern.
if grep -nE 'pgrep +-f|pkill +-f' "$SCRIPT" | grep -v '^[0-9]*:#' >/dev/null; then fail "docker-heavy.sh uses pgrep -f or pkill -f"; fi; ok

# --list with nothing running.
dh_rc --list
[ "$LAST_RC" = 0 ] || fail "--list exits 0, got $LAST_RC"; ok
case "$LAST_OUT" in *'heavy slot: free'*'light slot: free'*) ok ;; *) fail "--list on an idle machine: $LAST_OUT" ;; esac

# --cancel with nothing matching exits 1 and says so.
dh_rc --cancel nope
[ "$LAST_RC" = 1 ] || fail "--cancel of an unknown tag exits 1, got $LAST_RC: $LAST_OUT"; ok
case "$LAST_OUT" in *'nothing matched tag nope'*) ok ;; *) fail "--cancel names the tag it did not find: $LAST_OUT" ;; esac
dh_rc --cancel 'bad tag;rm'
[ "$LAST_RC" = 2 ] || fail "a tag with spaces or shell characters is refused (exit 2), got $LAST_RC"; ok

# A heavy job holds the slot, a second heavy job waits, a light job runs beside them.
cd "$TMP"
dh --tag t1 -- docker run --rm img heavy-work >"$TMP/t1.out" 2>&1 &
wait_for "$HOME_T/.cache/docker-heavy/lock.holder" || fail "the heavy job took the slot"
dh --tag t2 -- docker run --rm img second >"$TMP/t2.out" 2>&1 &
wait_for "$HOME_T/.cache/docker-heavy/jobs/t2.job" || fail "the waiting job wrote its job file"
dh --light --tag t3 -- docker run --rm img resize >"$TMP/t3.out" 2>&1 &
wait_for "$HOME_T/.cache/docker-heavy/light.lock.holder" || fail "the light job took its own slot while the heavy slot was busy"; ok
sleep 0.3
dh_rc --list
[ "$LAST_RC" = 0 ] || fail "--list while busy exits 0"; ok
case "$LAST_OUT" in *'heavy slot: held by tag t1 since '*) ok ;; *) fail "--list names the heavy holder's tag and start time: $LAST_OUT" ;; esac
case "$LAST_OUT" in *'light slot: held by tag t3 since '*) ok ;; *) fail "--list names the light holder: $LAST_OUT" ;; esac
case "$LAST_OUT" in *'waiting: t2 (heavy slot) since '*) ok ;; *) fail "--list names the waiting job and when it began waiting: $LAST_OUT" ;; esac
case "$LAST_OUT" in *'waiting: t1'*|*'waiting: t3'*) fail "--list shows a running job as waiting: $LAST_OUT" ;; *) ok ;; esac

# --light defaults to 4 CPUs and 4g; heavy keeps 8 and 12g.
grep -q -- '--memory 4g --memory-swap 4g --cpus 4 ' "$ST/run.log" || fail "the light job runs with 4g and 4 cpus: $(cat "$ST/run.log")"; ok
grep -q -- '--memory 12g --memory-swap 12g --cpus 8 ' "$ST/run.log" || fail "the heavy job keeps 12g and 8 cpus"; ok
grep -q -- '--label docker-heavy=t1 ' "$ST/run.log" || fail "the run carries the tag as its label"; ok

# --cancel on a waiting job stops it and nothing else.
dh_rc --cancel t2
[ "$LAST_RC" = 0 ] || fail "--cancel of a waiting job exits 0, got $LAST_RC: $LAST_OUT"; ok
sleep 0.5
[ ! -e "$HOME_T/.cache/docker-heavy/jobs/t2.job" ] || fail "the cancelled waiting job's file is gone"; ok
dh_rc --list
case "$LAST_OUT" in *'waiting:'*) fail "no job waits after the cancel: $LAST_OUT" ;; *) ok ;; esac
case "$LAST_OUT" in *'held by tag t1'*) ok ;; *) fail "the running job was left alone: $LAST_OUT" ;; esac

# --cancel on a running job kills its labelled container and stops the script, which frees the slot.
echo cid-aaa >"$ST/ids-t1"
dh_rc --cancel t1
[ "$LAST_RC" = 0 ] || fail "--cancel of a running job exits 0, got $LAST_RC: $LAST_OUT"; ok
grep -q 'cid-aaa' "$ST/kill.log" || fail "the labelled container was killed"; ok
for i in $(seq 1 50); do [ ! -e "$HOME_T/.cache/docker-heavy/lock.holder" ] && break; sleep 0.1; done
[ ! -e "$HOME_T/.cache/docker-heavy/lock.holder" ] || fail "the slot's holder file is gone after the cancel"; ok
rm -f "$ST/ids-t1"   # the killed container is gone
dh_rc --cancel t1
[ "$LAST_RC" = 1 ] || fail "cancelling the same tag again matches nothing (exit 1), got $LAST_RC"; ok

# A container with the label and no script (the outer timeout case) is still killed by --cancel.
echo cid-bbb >"$ST/ids-orphan"
dh_rc --cancel orphan
[ "$LAST_RC" = 0 ] || fail "--cancel kills a labelled container with no script running, got $LAST_RC"; ok
grep -q 'cid-bbb' "$ST/kill.log" || fail "the orphan container was killed"; ok

# A stale job file (left by a SIGKILL) whose pid now belongs to another process is never signalled: --cancel and --list drop the file instead.
# Case 1: the pid is reused by an unrelated process, start time differs. Case 2: same start time but the process is not docker-heavy.sh
# (a Codex lane's command line mentions .cache/docker-heavy, so a name match alone is not enough).
JD="$HOME_T/.cache/docker-heavy/jobs"; mkdir -p "$JD"
/bin/sleep 300 & victim=$!
vstart=$(cut -d' ' -f1- "/proc/$victim/stat"); vstart=${vstart##*) }; set -- $vstart; vstart=${20}
printf '%s\twaiting\theavy\t1\t1\t%s\tdocker x\n' "$victim" 1 >"$JD/stale1.job"
dh_rc --cancel stale1
[ "$LAST_RC" = 1 ] || fail "a stale job file with a reused pid matches nothing (exit 1), got $LAST_RC: $LAST_OUT"; ok
kill -0 "$victim" 2>/dev/null || fail "the unrelated process was killed by --cancel"; ok
[ ! -e "$JD/stale1.job" ] || fail "the stale job file is removed"; ok
printf '%s\twaiting\theavy\t1\t1\t%s\tdocker x\n' "$victim" "$vstart" >"$JD/stale2.job"
dh_rc --cancel stale2
[ "$LAST_RC" = 1 ] || fail "a job file whose pid runs another program matches nothing, got $LAST_RC: $LAST_OUT"; ok
kill -0 "$victim" 2>/dev/null || fail "a process with the right start time but another script was killed"; ok
printf '%s\twaiting\theavy\t1\t1\t%s\tdocker x\n' "$victim" 1 >"$JD/stale3.job"
dh_rc --list
[ ! -e "$JD/stale3.job" ] || fail "--list drops a stale job file"; ok
case "$LAST_OUT" in *stale3*) fail "--list shows a stale job: $LAST_OUT" ;; *) ok ;; esac
kill "$victim" 2>/dev/null || true; wait "$victim" 2>/dev/null || true

# The header says the timeout counts run time only and warns about outer timeouts.
grep -q 'counts RUN time only' "$SCRIPT" && grep -q 'must not wrap docker-heavy.sh in an outer' "$SCRIPT" || fail "the header explains the timeout"; ok

echo "ok - $checks checks passed"
