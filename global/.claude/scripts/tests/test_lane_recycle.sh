#!/usr/bin/env bash
# Tests for lane.sh --recycle and --ctx (lanes never auto-compact; his rule, 20 Oct). A fake tmux plays the lane: it writes what is typed
# into a fake transcript, starts a new transcript on /clear, and writes handoff.md when it sees the hand-off request.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-recycle.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; REPO="$TMP/repo"; ST="$TMP/fake"
HAND="$HOME_T/.claude/state/lanes/2026-10-21-rl"; INDEX="$HOME_T/.claude/state/lanes/index.tsv"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$BIN" "$REPO" "$ST" "$HAND" "$PROJ"
echo 'do the thing' >"$HAND/prompt.md"
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"

cat >"$BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exec /bin/sleep 0.02
STUB
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
# fake lane window "rl-lane": send-keys -l <text> types, C-m submits
case "$1" in
  has-session) exit 0 ;;
  list-windows) echo rl-lane ;;
  capture-pane) cat "$FAKE_ST/pane" ;;
  send-keys)
    if [ "${4:-}" = -l ]; then printf '%s' "${6:-}" >"$FAKE_ST/pending"
    elif [ "${4:-}" = C-m ] && [ -s "$FAKE_ST/pending" ]; then
      python3 -I - "$FAKE_ST" "$FAKE_PROJ" "$(cat "$FAKE_ST/pending")" <<'PY'
import json, os, sys
st, proj, text = sys.argv[1:4]
open(os.path.join(st, "pending"), "w").close()
open(os.path.join(st, "sent.log"), "a").write(text + "\n")
cur = open(os.path.join(st, "cur")).read().strip()
if text == "/clear":
    n = int(open(os.path.join(st, "n")).read()) + 1 if os.path.exists(os.path.join(st, "n")) else 1
    open(os.path.join(st, "n"), "w").write(str(n))
    open(os.path.join(st, "cur"), "w").write("new-sess-%d" % n)
    sys.exit(0)
with open(os.path.join(proj, cur + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
if "recycle line" in text and os.environ.get("FAKE_WRITE_HANDOFF") == "1":
    open(os.environ["FAKE_HANDOFF"], "w").write("# handoff\nnext step: carry on\n")
PY
    fi ;;
esac
STUB
chmod +x "$BIN/sleep" "$BIN/tmux"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
assert_exit() { [ "$2" = "$1" ] || fail "$3 (expected exit $1, got $2)"; ok; }

lane() {  # lane <args...>: run lane.sh against the fake home and window
  set +e
  LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_ST="$ST" FAKE_PROJ="$PROJ" FAKE_HANDOFF="$HAND/handoff.md" bash "$SCRIPT" "$@" 2>&1)"
  LAST_RC=$?
  set -e
}
usage_rec() {  # usage_rec <total-tokens>: one assistant turn whose context is that many tokens
  printf '{"type":"assistant","message":{"role":"assistant","usage":{"input_tokens":10,"cache_read_input_tokens":%d,"cache_creation_input_tokens":990}}}\n' "$(($1 - 1000))"
}
add_row() {  # add_row <name> <billing> <session>
  printf '%s\t%s\t%s\tsonnet\t%s\t%s\t%s\t%s\t-\thigh\toff\n' "$(date +%F)" "$1" "$REPO" "$2" "$HAND/prompt.md" "$HAND/report.md" "$3" >>"$INDEX"
}
reset_fake() { rm -f "$ST"/pending "$ST"/sent.log "$ST"/n "$HAND/handoff.md" "$HOME_T/.claude/state/lanes/recycle.log"; echo old-sess >"$ST/cur"; rm -f "$PROJ"/new-sess-*.jsonl; }

# --ctx reads the last assistant usage in the lane's transcript, in K.
add_row rl-lane subscription old-sess
usage_rec 130000 >"$PROJ/old-sess.jsonl"
lane --ctx rl-lane
assert_exit 0 "$LAST_RC" '--ctx exits 0'
[ "$LAST_OUT" = 130 ] || fail "--ctx on a 130000-token transcript prints 130, got: $LAST_OUT"; ok
lane --ctx nobody
[ "$LAST_OUT" = '?' ] || fail "--ctx for an unknown lane prints ?, got: $LAST_OUT"; ok
# A lane started a moment ago has "-" for its session id until --status fills it in: --ctx finds its transcript by start time.
printf '%s\tnew-lane\t%s\tsonnet\tsubscription\t%s\t%s\t-\t%s\thigh\toff\n' "$(date +%F)" "$REPO" "$HAND/prompt.md" "$HAND/report.md" "$(($(date +%s) - 5))" >>"$INDEX"
usage_rec 88000 >"$PROJ/fresh-sess.jsonl"
lane --ctx new-lane
[ "$LAST_OUT" = 88 ] || fail "--ctx finds a fresh lane transcript when the index id is blank, got: $LAST_OUT"; ok
rm -f "$PROJ/fresh-sess.jsonl"; sed -i '/\tnew-lane\t/d' "$INDEX"
add_row cx-lane codex -
lane --ctx cx-lane
[ "$LAST_OUT" = '?' ] || fail "--ctx for a Codex lane prints ?, got: $LAST_OUT"; ok

# --recycle --dry-run shows the steps in order and touches nothing.
reset_fake
lane --dry-run --recycle rl-lane
assert_exit 0 "$LAST_RC" '--recycle --dry-run exits 0'
n1="$(grep -n '1\. wait for the lane' <<<"$LAST_OUT" | cut -d: -f1)"; n2="$(grep -n '2\. send: Hub: your context is at 130K' <<<"$LAST_OUT" | cut -d: -f1)"
n3="$(grep -n '3\. wait for .*handoff.md to change' <<<"$LAST_OUT" | cut -d: -f1)"; n4="$(grep -n '4\. send: /clear' <<<"$LAST_OUT" | cut -d: -f1)"
n5="$(grep -n '5\. send: Hub: your context was cleared' <<<"$LAST_OUT" | cut -d: -f1)"; n6="$(grep -n '6\. append an index row' <<<"$LAST_OUT" | cut -d: -f1)"
[ -n "$n1" ] && [ "$n1" -lt "$n2" ] && [ "$n2" -lt "$n3" ] && [ "$n3" -lt "$n4" ] && [ "$n4" -lt "$n5" ] && [ "$n5" -lt "$n6" ] || fail "dry-run steps out of order: $LAST_OUT"; ok
assert_contains "$LAST_OUT" "Read $HAND/prompt.md, then $HAND/handoff.md" 'the re-prime reads prompt.md, then handoff.md'
[ ! -e "$ST/sent.log" ] || fail '--dry-run typed into the window'; ok
[ ! -e "$HAND/handoff.md" ] || fail '--dry-run wrote handoff.md'; ok

# A real recycle: hand-off request, handoff.md, /clear, re-prime, in that order; the index row follows the new session.
reset_fake; rows_before="$(wc -l <"$INDEX")"
FAKE_WRITE_HANDOFF=1 lane --recycle rl-lane
assert_exit 0 "$LAST_RC" '--recycle exits 0'
assert_contains "$LAST_OUT" 'recycled rl-lane' 'the recycle says what it did'
[ -s "$HAND/handoff.md" ] || fail 'handoff.md not written'; ok
mapfile -t sent <"$ST/sent.log"
[ "${#sent[@]}" = 3 ] || fail "expected 3 things typed into the lane, got ${#sent[@]}: ${sent[*]}"; ok
case "${sent[0]}" in *"write $HAND/handoff.md"*) ok ;; *) fail "first message asks for handoff.md: ${sent[0]}" ;; esac
[ "${sent[1]}" = /clear ] || fail "second is /clear, got: ${sent[1]}"; ok
case "${sent[2]}" in *"Read $HAND/prompt.md, then $HAND/handoff.md"*) ok ;; *) fail "third is the re-prime: ${sent[2]}" ;; esac
[ "$(wc -l <"$INDEX")" = $((rows_before + 1)) ] || fail 'one new index row'; ok
last="$(tail -1 "$INDEX")"
IFS=$'\t' read -r _d iname _dir _m _b iprompt irep isid _st _eff _conn <<<"$last"
[ "$iname" = rl-lane ] && [ "$isid" = new-sess-1 ] || fail "index row names the new session: $last"; ok
[ "$iprompt" = "$HAND/prompt.md" ] && [ "$irep" = "$HAND/report.md" ] || fail "index row keeps prompt and report: $last"; ok
lane --ctx rl-lane
[ "$LAST_OUT" != 130 ] || fail '--ctx still reads the old transcript after the recycle'; ok
[ -d "$HOME_T/.claude/state/lanes/.recycle-rl-lane.lock" ] && fail 'recycle lock left behind'; ok
grep -q "recycled" "$HOME_T/.claude/state/lanes/recycle.log" || fail 'recycle.log has no recycled row'; ok

# A lane that never writes handoff.md is NOT cleared.
reset_fake; rows_before="$(wc -l <"$INDEX")"
FAKE_WRITE_HANDOFF=0 LANE_RECYCLE_WAIT_SECS=2 lane --recycle rl-lane
assert_exit 1 "$LAST_RC" 'no handoff.md: the recycle fails'
assert_contains "$LAST_OUT" 'NOT cleared' 'the failure says the lane kept its context'
assert_not_contains "$(cat "$ST/sent.log")" '/clear' 'no /clear without a handoff.md'
[ "$(wc -l <"$INDEX")" = "$rows_before" ] || fail 'a failed recycle adds no index row'; ok
grep -q $'\tFAILED\t' "$HOME_T/.claude/state/lanes/recycle.log" || fail 'recycle.log has no FAILED row'; ok
[ -d "$HOME_T/.claude/state/lanes/.recycle-rl-lane.lock" ] && fail 'recycle lock left behind after a failure'; ok

# An old handoff.md from before does not count: it has to change.
reset_fake; echo "old hand-off" >"$HAND/handoff.md"; touch -d '-1 hour' "$HAND/handoff.md"
FAKE_WRITE_HANDOFF=0 LANE_RECYCLE_WAIT_SECS=2 lane --recycle rl-lane
assert_exit 1 "$LAST_RC" 'a stale handoff.md does not stand in for a new one'
assert_not_contains "$(cat "$ST/sent.log")" '/clear' 'no /clear on a stale handoff.md'

# A lane mid-turn (spinner) is not interrupted.
reset_fake; printf '%s\n' '⏺ Reading' '✻ Thinking… (12s · esc to interrupt)' '❯ ' >"$ST/pane"
LANE_RECYCLE_IDLE_SECS=2 lane --recycle rl-lane
assert_exit 1 "$LAST_RC" 'a lane that stays mid-turn is not recycled'
[ ! -e "$ST/sent.log" ] || fail 'typed into a working lane'; ok
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"

# Headless lanes have no window to recycle; a recycle already running blocks a second.
lane --recycle cx-lane
assert_exit 1 "$LAST_RC" 'a Codex lane is refused'
assert_contains "$LAST_OUT" 'headless' 'the refusal says why'
reset_fake; mkdir -p "$HOME_T/.claude/state/lanes/.recycle-rl-lane.lock"
FAKE_WRITE_HANDOFF=1 lane --recycle rl-lane
assert_exit 1 "$LAST_RC" 'a second recycle of the same lane is refused'
assert_contains "$LAST_OUT" 'already being recycled' 'the refusal names the lock'
rmdir "$HOME_T/.claude/state/lanes/.recycle-rl-lane.lock"
lane --recycle nobody
assert_exit 1 "$LAST_RC" 'an unknown lane is refused'

printf 'ok - %s checks passed\n' "$checks"
