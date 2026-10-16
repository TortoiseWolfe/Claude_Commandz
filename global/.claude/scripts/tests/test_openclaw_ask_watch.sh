#!/usr/bin/env bash
# openclaw_ask_watch.sh against a throwaway drop folder and a stub poll. Nothing real is read, consumed or acted on.
# Prints the milliseconds from a dropped item to the poll starting, then PASS/FAIL per check. Exit 0 only if all pass.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WATCH="$HERE/../openclaw_ask_watch.sh"
T=$(mktemp -d)
trap 'kill "${WPID:-0}" 2>/dev/null; wait 2>/dev/null; rm -rf "$T"' EXIT
mkdir -p "$T/stage" "$T/state"
cat > "$T/poll.sh" <<'EOF'
#!/usr/bin/env bash
date +%s%N >> "$OC_TEST_OUT"
[ -n "${OC_TEST_CONSUME:-}" ] && rm -f "$OC_STAGE_DIR"/tray-*
sleep "${OC_TEST_POLL_TIME:-0}"
exit 0
EOF
chmod +x "$T/poll.sh"
export OC_STAGE_DIR="$T/stage" OC_STATE_DIR="$T/state" OC_POLL_SCRIPT="$T/poll.sh" OC_TEST_OUT="$T/runs"
: > "$T/runs"
ok=0
check() { if [ "$2" = 0 ]; then echo "PASS $1"; else echo "FAIL $1"; ok=1; fi; }
runs() { wc -l < "$T/runs"; }
ms() { echo $(( $(date +%s%N) / 1000000 )); }
drop() { # the way the hook writes: a temp name, then a rename
  printf '{"id":"x","source":"tray","text":"status","ts":1}' > "$T/stage/.tmp-$1"
  mv "$T/stage/.tmp-$1" "$T/stage/tray-$1.json"
}

"$WATCH" & WPID=$!
sleep 1.5

echo "== latency, item dropped -> poll started (poll consumes the item)"
export OC_TEST_CONSUME=1
worst=0
for i in 1 2 3 4 5; do
  n=$(runs); t0=$(ms); drop "lat$i"
  for _ in $(seq 1 100); do [ "$(runs)" -gt "$n" ] && break; sleep 0.05; done
  d=$(( $(date +%s%N) / 1000000 - t0 ))
  [ "$(runs)" -gt "$n" ] || d=99999
  echo "drop $i: ${d} ms"
  [ "$d" -gt "$worst" ] && worst=$d
  sleep 2.2
done
echo "worst ${worst} ms"
check "every dropped item starts the poll within 3 s" $([ "$worst" -lt 3000 ] && echo 0 || echo 1)
unset OC_TEST_CONSUME

echo "== a writer's temp file does not start the poll"
n=$(runs); printf x > "$T/stage/.tmp-half"; sleep 2
check "temp file ignored" $([ "$(runs)" = "$n" ] && echo 0 || echo 1)
rm -f "$T/stage/.tmp-half"

echo "== a burst of 6 files is debounced, and a file the poll leaves behind does not make it spin"
n=$(runs)
for i in 1 2 3 4 5 6; do drop "burst$i"; done
sleep 6
r=$(( $(runs) - n ))
echo "poll runs for the burst: $r"
check "burst runs the poll at most twice" $([ "$r" -ge 1 ] && [ "$r" -le 2 ] && echo 0 || echo 1)
n=$(runs); sleep 5
check "files left in the folder do not re-run it" $([ "$(runs)" = "$n" ] && echo 0 || echo 1)
rm -f "$T/stage"/tray-*

echo "== an item that lands while a poll is running gets its own run"
export OC_TEST_POLL_TIME=2
sleep 1; n=$(runs); drop "during1"
for _ in $(seq 1 60); do [ "$(runs)" -gt "$n" ] && break; sleep 0.05; done
drop "during2"
sleep 7
r=$(( $(runs) - n ))
check "second item after the first poll started is not lost ($r runs)" $([ "$r" -ge 2 ] && echo 0 || echo 1)
unset OC_TEST_POLL_TIME
rm -f "$T/stage"/tray-*
sleep 3

echo "== a second copy exits at once and leaves one watcher"
t0=$(ms); timeout 5 "$WATCH"; rc=$?
d=$(( $(ms) - t0 ))
check "second instance exits 0 in under 2 s (rc=$rc, ${d} ms)" $([ "$rc" = 0 ] && [ "$d" -lt 2000 ] && echo 0 || echo 1)
check "the first watcher is still alive" $(kill -0 "$WPID" 2>/dev/null && echo 0 || echo 1)

echo "== a missing folder does not kill it"
mv "$T/stage" "$T/stage-gone"; sleep 1
check "alive with no folder" $(kill -0 "$WPID" 2>/dev/null && echo 0 || echo 1)
mv "$T/stage-gone" "$T/stage"

exit "$ok"
