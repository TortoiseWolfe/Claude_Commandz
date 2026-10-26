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
# A queued input that "runs" (FAKE_QUEUE=run) is logged as dequeued after a few sleeps: the lane works through its queue while lane.sh waits.
if [ -f "$FAKE_ST/queue-run" ]; then
  left=$(( $(cat "$FAKE_ST/queue-run") - 1 ))
  if [ "$left" -le 0 ]; then
    printf '{"type":"queue-operation","operation":"dequeue"}\n' >>"$FAKE_PROJ/$(cat "$FAKE_ST/cur").jsonl"; rm -f "$FAKE_ST/queue-run" "$FAKE_ST/queued"
  else echo "$left" >"$FAKE_ST/queue-run"; fi
fi
exec /bin/sleep 0.02
STUB
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
# fake lane window "rl-lane": send-keys -l <text> types, C-m submits
case "$1" in
  has-session) exit 0 ;;
  list-windows) printf "%s\n" rl-lane sh-lane q-lane rk-lane rk-fast pol-opus pol-ent pol-acme-mail pol-site pol-sonnet ;;
  capture-pane) cat "$FAKE_ST/pane" ;;
  send-keys)
    printf '%s\n' "${4:-}" >>"$FAKE_ST/keys.log"
    # Escape puts queued input back in the input line, which Claude Code logs as popAll (FAKE_QUEUE=stubborn: it does not)
    if [ "${4:-}" = Escape ] && [ -f "$FAKE_ST/queued" ] && [ "${FAKE_QUEUE:-}" != stubborn ]; then
      printf '{"type":"queue-operation","operation":"popAll","content":"queued text"}\n' >>"$FAKE_PROJ/$(cat "$FAKE_ST/cur").jsonl"; rm -f "$FAKE_ST/queued"
    elif [ "${4:-}" = -l ]; then printf '%s' "${6:-}" >"$FAKE_ST/pending"
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
# /model is a local command: it only prints a confirmation line on the screen, never in the transcript (22 Oct).
if text.startswith("/model "):
    if not os.path.exists(os.path.join(st, "no-pane-line")):
        open(os.path.join(st, "pane"), "a").write("Set model to %s 5.5 and saved as your default for new sessions\n" % text.split(None, 1)[1].capitalize())
    sys.exit(0)
with open(os.path.join(proj, cur + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
if "recycle line" in text and os.environ.get("FAKE_WRITE_HANDOFF") == "1":
    open(os.environ["FAKE_HANDOFF"], "w").write("# handoff\nnext step: carry on\n")
    if os.environ.get("FAKE_QUEUE"):
        with open(os.path.join(proj, cur + ".jsonl"), "a") as f:
            f.write(json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "queued text"}) + "\n")
        open(os.path.join(st, "queued"), "w").close()
        if os.environ["FAKE_QUEUE"] == "run":
            open(os.path.join(st, "queue-run"), "w").write("5")
    if os.environ.get("FAKE_IDLE_AFTER") == "1":
        open(os.path.join(st, "pane"), "w").write("\u23fa handoff written\n\u273b Worked for 1m 9s \u00b7 done 1:09 PM\n\u276f \n")
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
reset_fake() { rm -f "$ST"/pending "$ST"/sent.log "$ST"/keys.log "$ST"/queued "$ST"/queue-run "$HAND/handoff.md" "$HAND"/handoff-prev*.md "$HOME_T/.claude/state/lanes/recycle.log"; echo old-sess >"$ST/cur"; rm -f "$PROJ"/new-sess-*.jsonl; }

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
[ "$LAST_OUT" = '?' ] || fail "--ctx does not guess a blank id from the newest transcript, got: $LAST_OUT"; ok
mkdir -p "$HOME_T/.claude/state/lanes/sessions"; printf 'fresh-sess\t%s\t-\n' "$(date +%s)" >"$HOME_T/.claude/state/lanes/sessions/new-lane"
lane --ctx new-lane
[ "$LAST_OUT" = 88 ] || fail "--ctx finds a fresh lane's transcript by the session its hook recorded, got: $LAST_OUT"; ok
rm -f "$PROJ/fresh-sess.jsonl"; sed -i '/\tnew-lane\t/d' "$INDEX"
add_row cx-lane codex -
lane --ctx cx-lane
[ "$LAST_OUT" = '?' ] || fail "--ctx for a Codex lane prints ?, got: $LAST_OUT"; ok

# --turn says whether the lane's last turn only answered a Monitor event (lane_watch.sh then does not wake the hub), from its transcript.
MON='<task-notification>\n<task-id>b1</task-id>\n<summary>Monitor event: \"download progress\"</summary>\n<event>10%</event>\n</task-notification>'
printf '{"type":"user","message":{"role":"user","content":"%s"}}\n{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"10 percent, still going."}]}}\n' "$MON" >"$PROJ/old-sess.jsonl"
lane --turn rl-lane
[ "$LAST_OUT" = monitor ] || fail "--turn on a Monitor tick answered without a question prints monitor, got: $LAST_OUT"; ok
printf '{"type":"user","message":{"role":"user","content":"merge it"}}\n{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Merged."}]}}\n' >"$PROJ/old-sess.jsonl"
lane --turn rl-lane
[ "$LAST_OUT" = real ] || fail "--turn on a typed turn prints real, got: $LAST_OUT"; ok
lane --turn nobody
[ "$LAST_OUT" = '?' ] || fail "--turn for an unknown lane prints ?, got: $LAST_OUT"; ok
usage_rec 130000 >"$PROJ/old-sess.jsonl"

# --recycle --dry-run shows the steps in order and touches nothing.
reset_fake
lane --dry-run --recycle rl-lane
assert_exit 0 "$LAST_RC" '--recycle --dry-run exits 0'
n1="$(grep -n '1\. wait only for any dialog' <<<"$LAST_OUT" | cut -d: -f1)"; n2="$(grep -n '2\. send: Hub: your context is at 130K' <<<"$LAST_OUT" | cut -d: -f1)"
n3="$(grep -n '3\. wait for .*handoff.md to change' <<<"$LAST_OUT" | cut -d: -f1)"; n4="$(grep -n '4\. send: /clear' <<<"$LAST_OUT" | cut -d: -f1)"
n5="$(grep -n '5\. send: Hub: your context was cleared' <<<"$LAST_OUT" | cut -d: -f1)"; n6="$(grep -n '6\. append an index row' <<<"$LAST_OUT" | cut -d: -f1)"
[ -n "$n1" ] && [ "$n1" -lt "$n2" ] && [ "$n2" -lt "$n3" ] && [ "$n3" -lt "$n4" ] && [ "$n4" -lt "$n5" ] && [ "$n5" -lt "$n6" ] || fail "dry-run steps out of order: $LAST_OUT"; ok
assert_contains "$LAST_OUT" "Read $HAND/prompt.md, then $HAND/handoff.md" 'the re-prime reads prompt.md, then handoff.md'
[ ! -e "$ST/sent.log" ] || fail '--dry-run typed into the window'; ok
[ ! -e "$HAND/handoff.md" ] || fail '--dry-run wrote handoff.md'; ok

# A real recycle: hand-off request, handoff.md, /clear, re-prime, in that order; the index row follows the new session.
# 23 Oct: a session in the lane's folder that is not the lane's (the hub's own) was born meanwhile; the row must not take it.
reset_fake; rows_before="$(wc -l <"$INDEX")"
usage_rec 250000 >"$PROJ/hub-decoy.jsonl"
FAKE_WRITE_HANDOFF=1 lane --recycle rl-lane
assert_exit 0 "$LAST_RC" '--recycle exits 0'
assert_contains "$LAST_OUT" 'recycled rl-lane' 'the recycle says what it did'
[ -s "$HAND/handoff.md" ] || fail 'handoff.md not written'; ok
mapfile -t sent <"$ST/sent.log"
[ "${#sent[@]}" = 3 ] || fail "expected 3 things typed into the lane, got ${#sent[@]}: ${sent[*]}"; ok
case "${sent[0]}" in *"writing $HAND/handoff.md"*) ok ;; *) fail "first message asks for handoff.md: ${sent[0]}" ;; esac
[ "${sent[1]}" = /clear ] || fail "second is /clear, got: ${sent[1]}"; ok
case "${sent[2]}" in *"Read $HAND/prompt.md, then $HAND/handoff.md"*) ok ;; *) fail "third is the re-prime: ${sent[2]}" ;; esac
[ "$(wc -l <"$INDEX")" = $((rows_before + 1)) ] || fail 'one new index row'; ok
last="$(tail -1 "$INDEX")"
IFS=$'\t' read -r _d iname _dir _m _b iprompt irep isid _st _eff _conn <<<"$last"
[ "$iname" = rl-lane ] && [ "$isid" = new-sess-1 ] || fail "index row names the new session: $last"; ok
rm -f "$PROJ/hub-decoy.jsonl"
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

# A lane mid-turn (spinner) is asked at once, not waited for: a long turn would outrun the backstop (21 Oct, one 15-minute turn went 120K to
# 168K and compacted, while the old recycle waited 300s for an idle that never came). It finishes the step in hand and writes handoff.md.
reset_fake; printf '%s\n' '⏺ Reading' '✻ Thinking… (12s · esc to interrupt)' '❯ ' >"$ST/pane"
FAKE_WRITE_HANDOFF=1 FAKE_IDLE_AFTER=1 LANE_RECYCLE_IDLE_SECS=2 lane --recycle rl-lane
[ "$LAST_RC" = 0 ] || fail "mid-turn recycle failed: $LAST_OUT"; ok
assert_exit 0 "$LAST_RC" 'a lane in the middle of a turn is recycled without waiting for the turn to end'
mapfile -t sent <"$ST/sent.log"
case "${sent[0]}" in *"writing $HAND/handoff.md"*) ok ;; *) fail "the hand-off request was typed into the working lane: ${sent[0]}" ;; esac
assert_contains "${sent[0]}" 'Finish only the step in hand' 'the request says to finish only the step in hand'
assert_contains "${sent[0]}" 'start no new step' 'the request says to start no new step'
assert_contains "${sent[0]}" 'very next action' 'the request makes the hand-off the very next action'
assert_contains "${sent[0]}" 'ready to recycle' 'the request asks the lane to stop and say ready'
assert_contains "${sent[0]}" 'recycle line (100K)' "the request names the lane's own line"
# A dialog on screen holds the request back (typing would answer it) and the lane is left alone.
reset_fake; printf '%s\n' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel' >"$ST/pane"
FAKE_WRITE_HANDOFF=1 LANE_RECYCLE_IDLE_SECS=2 lane --recycle rl-lane
assert_exit 1 "$LAST_RC" 'a lane stuck on a dialog is not recycled'
assert_contains "$LAST_OUT" 'dialog' 'the failure names the dialog'
[ ! -e "$ST/sent.log" ] || fail 'typed into a lane that was on a dialog'; ok
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"

# --send (which --recycle uses) looks for a dialog before typing and before each Enter, so its keys never answer a permission prompt.
reset_fake; printf '%s\n' 'Do you want to proceed?' '❯ 1. Yes' 'Esc to cancel' >"$ST/pane"
lane --send rl-lane 'hello 1'
assert_exit 1 "$LAST_RC" '--send to a lane on a dialog is refused'
assert_contains "$LAST_OUT" 'NOT SENT' 'the refusal says nothing was sent'
[ ! -e "$ST/sent.log" ] && [ ! -s "$ST/pending" ] || fail 'keys were typed into a dialog'; ok
printf '%s\n' '⏺ Merged.' '✻ Worked for 4m 2s · done 1:05 PM' '❯ ' >"$ST/pane"

# Each lane has its own line, kept in a twelfth index column across a recycle. A first recycle after a long run keeps it; one within 20
# minutes of the lane's start or last recycle means its working set outgrew the line, so the next line is 20K higher (never above
# 0.84 x the window cap - 40K = 128K).
rk_last() { tail -1 "$INDEX" | cut -f12; }
printf '%s\trk-lane\t%s\tsonnet\tsubscription\t%s\t%s\tsess-rk\t%s\thigh\toff\t90\n' "$(date +%F)" "$REPO" "$HAND/prompt.md" "$HAND/report.md" "$(( $(date +%s) - 7200 ))" >>"$INDEX"
lane --recycle-point rk-lane; [ "$LAST_OUT" = 90 ] || fail "--recycle-point reads 90, got $LAST_OUT"; ok
lane --dry-run --recycle rk-lane
assert_contains "$LAST_OUT" 'recycle line 90K' 'the dry run shows the line the new row will carry'
printf '%s\trk-old\t%s\tsonnet\tsubscription\t%s\t%s\tsess-old2\t-\thigh\toff\n' "$(date +%F)" "$REPO" "$HAND/prompt.md" "$HAND/report.md" >>"$INDEX"
lane --dry-run --recycle rk-old
assert_contains "$LAST_OUT" 'recycle line 100K' 'a row without the column shows the default line'
printf '%s\trk-fast\t%s\tsonnet\tsubscription\t%s\t%s\tsess-fast\t%s\thigh\toff\t100\n' "$(date +%F)" "$REPO" "$HAND/prompt.md" "$HAND/report.md" "$(( $(date +%s) - 300 ))" >>"$INDEX"
cp "$PROJ/old-sess.jsonl" "$PROJ/sess-fast.jsonl"; cp "$PROJ/old-sess.jsonl" "$PROJ/sess-rk.jsonl"
reset_fake; cp "$PROJ/old-sess.jsonl" "$PROJ/sess-fast.jsonl" 2>/dev/null || true
echo sess-fast >"$ST/cur"
FAKE_WRITE_HANDOFF=1 lane --recycle rk-fast
[ "$LAST_RC" = 0 ] || fail "fast refill: $LAST_OUT"
assert_exit 0 "$LAST_RC" 'a fast refill is recycled'
[ "$(rk_last)" = 120 ] || fail "a recycle 5 minutes after the lane's start raises its line 100K -> 120K, got: $(rk_last)"; ok
assert_contains "$LAST_OUT" 'line 120K' 'the recycle says the new line'
# the new row's st is now, so an immediate second recycle goes up again, to the ceiling and no further
reset_fake; echo "$(cut -f8 "$INDEX" | tail -1)" >"$ST/cur"; cp "$PROJ/old-sess.jsonl" "$PROJ/$(cat "$ST/cur").jsonl"
FAKE_WRITE_HANDOFF=1 lane --recycle rk-fast
[ "$(rk_last)" = 128 ] || fail "the line stops at 128K (0.84 x 200K - 40K), got: $(rk_last)"; ok
reset_fake; echo "$(cut -f8 "$INDEX" | tail -1)" >"$ST/cur"; cp "$PROJ/old-sess.jsonl" "$PROJ/$(cat "$ST/cur").jsonl"
FAKE_WRITE_HANDOFF=1 lane --recycle rk-fast
[ "$(rk_last)" = 128 ] || fail "a line already at the ceiling stays there, got: $(rk_last)"; ok
reset_fake; echo sess-rk >"$ST/cur"
FAKE_WRITE_HANDOFF=1 lane --recycle rk-lane
[ "$(rk_last)" = 90 ] || fail "a lane that ran for two hours keeps its line (90K), got: $(rk_last)"; ok

qreset() {  # a fresh q-lane row on old-sess (a recycle moves the lane's row to the new session) and an old transcript with no queue ops
  reset_fake; usage_rec 130000 >"$PROJ/old-sess.jsonl"; add_row q-lane subscription old-sess
}
# 25 Oct, pokeys-site: input queued in the lane (typed mid-turn) survived /clear and reached the FRESH session, which wrote handoff.md and
# stopped, under its line, with nothing to recycle it. A recycle now makes sure nothing is queued before it types /clear.
qreset
FAKE_QUEUE=run FAKE_WRITE_HANDOFF=1 lane --recycle q-lane
assert_exit 0 "$LAST_RC" 'queued input that runs by itself lets the recycle go on'
assert_not_contains "$(cat "$ST/keys.log" 2>/dev/null)" Escape 'input that ran by itself is not popped with Escape'
mapfile -t sent <"$ST/sent.log"; [ "${#sent[@]}" = 3 ] || fail "a drained queue still gives 3 typed messages, got ${#sent[@]}"; ok

qreset
FAKE_QUEUE=stuck LANE_RECYCLE_QUEUE_SECS=2 FAKE_WRITE_HANDOFF=1 lane --recycle q-lane
assert_exit 0 "$LAST_RC" 'queued input that does not run is popped with Escape, then the recycle goes on'
assert_contains "$(cat "$ST/keys.log")" Escape 'Escape is pressed for stuck queued input'
assert_contains "$(cat "$ST/keys.log")" C-u 'the input line is cleared after Escape'
esc="$(grep -n '^Escape$' "$ST/keys.log" | head -1 | cut -d: -f1)"; cm="$(grep -c '^C-m$' "$ST/keys.log")"
[ -n "$esc" ] && [ "$cm" -ge 3 ] || fail "keys: Escape before the Enter that sends /clear ($esc, $cm Enters)"; ok
mapfile -t sent <"$ST/sent.log"; [ "${sent[1]}" = /clear ] || fail "/clear follows the drain, got: ${sent[1]}"; ok

qreset
FAKE_QUEUE=stubborn LANE_RECYCLE_QUEUE_SECS=2 FAKE_WRITE_HANDOFF=1 lane --recycle q-lane
assert_exit 1 "$LAST_RC" 'input that cannot be cleared fails the recycle'
assert_contains "$LAST_OUT" 'still queued' 'the failure says input is still queued'
assert_contains "$LAST_OUT" 'NOT cleared' 'the failure says the lane kept its context'
assert_not_contains "$(cat "$ST/sent.log")" '/clear' 'no /clear while input is queued'
grep -q $'\tFAILED\t' "$HOME_T/.claude/state/lanes/recycle.log" || fail 'recycle.log has a FAILED row for the stuck queue'; ok
[ -d "$HOME_T/.claude/state/lanes/.recycle-q-lane.lock" ] && fail 'recycle lock left behind after a stuck queue'; ok
qreset

# The previous handoff.md is kept as handoff-prev<N>.md before the request (handoff-prev.md counts as 1), newest 5 kept.
qreset; echo "first handoff" >"$HAND/handoff.md"
FAKE_WRITE_HANDOFF=1 lane --recycle q-lane
assert_exit 0 "$LAST_RC" 'a recycle with an old handoff.md'
[ "$(cat "$HAND/handoff-prev1.md" 2>/dev/null || cat "$HAND/handoff-prev2.md" 2>/dev/null)" = "first handoff" ] || fail 'the old handoff.md is kept as a handoff-prev file'; ok
qreset; for i in 2 3 4 5 6 7 8; do echo "prev $i" >"$HAND/handoff-prev$i.md"; touch -d "-$((10 - i)) hours" "$HAND/handoff-prev$i.md"; done
echo "prev 1" >"$HAND/handoff-prev.md"; touch -d '-9 hours' "$HAND/handoff-prev.md"; echo "current handoff" >"$HAND/handoff.md"
FAKE_WRITE_HANDOFF=1 lane --recycle q-lane
assert_exit 0 "$LAST_RC" 'a recycle with many handoff-prev files'
[ "$(cat "$HAND/handoff-prev9.md")" = "current handoff" ] || fail "the handoff being replaced becomes handoff-prev9.md (one past the highest), got: $(ls "$HAND")"; ok
[ "$(ls "$HAND"/handoff-prev*.md | wc -l)" = 5 ] || fail "only the newest 5 handoff-prev files are kept, got: $(ls "$HAND")"; ok
[ -e "$HAND/handoff-prev9.md" ] && [ -e "$HAND/handoff-prev8.md" ] && [ -e "$HAND/handoff-prev5.md" ] && [ ! -e "$HAND/handoff-prev4.md" ] || fail "the oldest are the ones dropped: $(ls "$HAND")"; ok
qreset; echo "keep me" >"$HAND/handoff-prev3.md"; rm -f "$HAND/handoff.md"
FAKE_WRITE_HANDOFF=1 LANE_HANDOFF_PREV_KEEP=2 lane --recycle q-lane
assert_exit 0 "$LAST_RC" 'a first recycle with no handoff.md yet'
[ ! -e "$HAND/handoff-prev4.md" ] || fail 'no handoff-prev made from a handoff.md that did not exist'; ok

# The request asks for a handoff a fresh session can use alone, and the re-prime sends it to prompt.md and handoff.md only.
qreset
lane --dry-run --recycle q-lane
assert_contains "$LAST_OUT" 'the next concrete step' 'the handoff request asks for the next concrete step'
assert_contains "$LAST_OUT" 'what is in flight' 'the handoff request asks for what is in flight'
assert_contains "$LAST_OUT" 'what to re-arm' 'the handoff request asks for what to re-arm'
assert_contains "$LAST_OUT" 'do not send the next session to read it' 'the request keeps handoff-prev out of the next session'
assert_contains "$LAST_OUT" 'ignore it' 'the re-prime tells a fresh session to ignore a leftover request'
assert_contains "$LAST_OUT" '1b. keep the current handoff.md' 'the dry run shows the rotation step'
assert_contains "$LAST_OUT" '3b. make sure no input is queued' 'the dry run shows the queue step'

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

# Model policy at a recycle (his rule, 22 Oct 8 PM: ten lanes ran on Opus all day, only one was entitled; the tier was checked at start only).
# Right after the /clear and before the re-prime the lane goes back to sonnet, unless it is a site-model lane or its row holds a reason.
# A mail lane is always sonnet.
USERSET="$HOME_T/.claude/settings.json"
printf '{\n  "theme": "dark"\n}\n' >"$USERSET"
SITE_P="$HAND/site-prompt.md"; echo 'Fit the photos in data/sites/example-01' >"$SITE_P"
pol_row() {  # pol_row <name> <model> <prompt> <reason or ->
  printf '%s\t%s\t%s\t%s\tsubscription\t%s\t%s\told-sess\t-\thigh\toff\t-\t%s\n' "$(date +%F)" "$1" "$REPO" "$2" "$3" "$HAND/report.md" "$4" >>"$INDEX"
}
pol_model() { tail -1 "$INDEX" | cut -f4; }
pol_ent() { tail -1 "$INDEX" | cut -f13; }
pol_row pol-opus opus "$HAND/prompt.md" -
pol_row pol-ent opus "$HAND/prompt.md" "design review of the router"
pol_row pol-acme-mail fable "$HAND/prompt.md" "claim-check pass"
pol_row pol-site opus "$SITE_P" -
pol_row pol-sonnet sonnet "$HAND/prompt.md" -
lane --tier pol-opus; [ "$LAST_OUT" = "$(printf 'opus\t-')" ] || fail "--tier of an unentitled opus lane prints opus and -, got: $LAST_OUT"; ok
lane --tier pol-ent; [ "$LAST_OUT" = "$(printf 'opus\tdesign review of the router')" ] || fail "--tier prints the reason, got: $LAST_OUT"; ok
lane --tier pol-site; [ "$LAST_OUT" = "$(printf 'opus\tsite-model')" ] || fail "--tier of a site-model lane prints site-model, got: $LAST_OUT"; ok
lane --tier pol-sonnet; [ "$LAST_OUT" = "$(printf 'sonnet\t-')" ] || fail "--tier of a sonnet lane, got: $LAST_OUT"; ok

reset_fake
lane --dry-run --recycle pol-opus
assert_exit 0 "$LAST_RC" 'dry run of an unentitled opus lane'
assert_contains "$LAST_OUT" '4b. set the model to the policy model (sonnet)' 'the dry run shows the model step'
assert_contains "$LAST_OUT" 'model sonnet and recycle line' 'the dry run says the new row carries sonnet'
lane --dry-run --recycle pol-ent
assert_contains "$LAST_OUT" 'policy model (opus)' 'the dry run of an entitled lane keeps opus'
lane --dry-run --recycle pol-acme-mail
assert_contains "$LAST_OUT" 'policy model (sonnet)' 'the dry run of a mail lane says sonnet'
[ ! -e "$ST/sent.log" ] || fail 'the model dry runs typed into the window'; ok

reset_fake
FAKE_WRITE_HANDOFF=1 lane --recycle pol-opus
assert_exit 0 "$LAST_RC" 'an unentitled opus lane is recycled'
mapfile -t sent <"$ST/sent.log"
[ "${#sent[@]}" = 4 ] || fail "hand-off request, /clear, /model, re-prime expected, got ${#sent[@]}: ${sent[*]}"; ok
[ "${sent[1]}" = /clear ] && [ "${sent[2]}" = "/model sonnet" ] || fail "/model sonnet is typed right after /clear, got: ${sent[1]} / ${sent[2]}"; ok
case "${sent[3]}" in *"Read $HAND/prompt.md"*) ok ;; *) fail "the re-prime comes after the model switch: ${sent[3]}" ;; esac
[ "$(pol_model)" = sonnet ] || fail "the new index row has the policy model, got: $(pol_model)"; ok
[ "$(tail -1 "$INDEX" | cut -f2)" = pol-opus ] || fail "the new row is the lane's own"; ok
grep -q $'\tmodel-switched\tfrom opus to sonnet' "$HOME_T/.claude/state/lanes/recycle.log" || fail 'recycle.log has no model-switched row'; ok
[ "$(cat "$USERSET")" = "$(printf '{\n  "theme": "dark"\n}\n')" ] || fail 'the recycle left the user default model alone'; ok
lane --tier pol-opus; [ "$LAST_OUT" = "$(printf 'sonnet\t-')" ] || fail "--tier follows the recycle, got: $LAST_OUT"; ok

reset_fake
FAKE_WRITE_HANDOFF=1 lane --recycle pol-ent
assert_exit 0 "$LAST_RC" 'an entitled opus lane is recycled'
assert_not_contains "$(cat "$ST/sent.log")" '/model' 'an entitled lane keeps its model: no /model typed'
[ "$(pol_model)" = opus ] && [ "$(pol_ent)" = "design review of the router" ] || fail "the new row keeps opus and the reason, got: $(tail -1 "$INDEX")"; ok

reset_fake
FAKE_WRITE_HANDOFF=1 lane --recycle pol-acme-mail
assert_exit 0 "$LAST_RC" 'a mail lane on fable is recycled'
assert_contains "$(cat "$ST/sent.log")" '/model sonnet' 'a mail lane goes back to sonnet even with a reason'
[ "$(pol_model)" = sonnet ] || fail "the mail lane's new row has sonnet, got: $(pol_model)"; ok

reset_fake
FAKE_WRITE_HANDOFF=1 lane --recycle pol-site
assert_exit 0 "$LAST_RC" 'a site-model lane is recycled'
assert_not_contains "$(cat "$ST/sent.log")" '/model' 'a site-model lane keeps opus: no /model typed'
[ "$(pol_model)" = opus ] || fail "the site-model lane's new row keeps opus, got: $(pol_model)"; ok

reset_fake
FAKE_WRITE_HANDOFF=1 lane --recycle pol-sonnet
assert_exit 0 "$LAST_RC" 'a sonnet lane is recycled'
assert_not_contains "$(cat "$ST/sent.log")" '/model' 'a sonnet lane is not touched'

# A switch the pane does not confirm is logged (as model-switch-failed, not FAILED: that word wakes lane_watch) and the recycle goes on.
pol_row pol-opus opus "$HAND/prompt.md" -
reset_fake; touch "$ST/no-pane-line"
LANE_SEND_CONFIRM_SECS=1 FAKE_WRITE_HANDOFF=1 lane --recycle pol-opus
assert_exit 0 "$LAST_RC" 'a model switch that is not confirmed does not fail the recycle'
assert_contains "$LAST_OUT" 'could not set pol-opus to sonnet' 'the recycle says the switch failed'
[ "$(pol_model)" = opus ] || fail "the new row keeps the model the lane is really on, got: $(pol_model)"; ok
grep -q $'\tmodel-switch-failed\t' "$HOME_T/.claude/state/lanes/recycle.log" || fail 'recycle.log has no model-switch-failed row'; ok
if grep -q $'\tFAILED\t' "$HOME_T/.claude/state/lanes/recycle.log"; then fail 'a model-switch failure must not be logged as FAILED'; fi; ok
rm -f "$ST/no-pane-line"

# 26 Oct: lane.sh --send typed a hub message into scout-run-2 while it was at 163K, past its 128K line and mid-turn. Claude Code queued it and
# the queued text blocked the watcher's recycle (the guard above failed safe). A lane past its line, or with a recycle under way, is not typed
# into: the words go to hub-pending.md in its hand-off folder, the recycle starts, and the re-prime sends the fresh session to read the file.
PEND="$HAND/hub-pending.md"
shreset() { reset_fake; rm -f "$PEND" "$HOME_T/.claude/state/lanes/.recycle-sh-lane.tried"; rmdir "$HOME_T/.claude/state/lanes/.recycle-sh-lane.lock" 2>/dev/null || true; usage_rec "$1" >"$PROJ/old-sess.jsonl"; sed -i '/\tsh-lane\t/d' "$INDEX"; add_row sh-lane subscription old-sess; }
wait_recycled() {  # wait up to 20s for the background recycle to log its row
  local i; for i in $(seq 1 100); do grep -q $'\tsh-lane\trecycled\t' "$HOME_T/.claude/state/lanes/recycle.log" 2>/dev/null && return 0; sleep 0.2; done; return 1
}
shreset 130000
FAKE_WRITE_HANDOFF=1 lane --send sh-lane 'please also merge the docs PR'
assert_exit 0 "$LAST_RC" '--send to a lane past its line exits 0'
assert_contains "$LAST_OUT" 'held for sh-lane' '--send says it held the text back'
assert_contains "$LAST_OUT" "130K, past its 100K line" '--send names the context and the line'
assert_contains "$LAST_OUT" "$PEND" '--send names the file it wrote'
assert_contains "$LAST_OUT" 'started its recycle' '--send says it started the recycle'
assert_contains "$(cat "$PEND")" 'please also merge the docs PR' 'hub-pending.md holds the text'
wait_recycled || fail 'the recycle --send started never finished'; ok
sent=(); while IFS= read -r l; do sent+=("$l"); done <"$ST/sent.log"
assert_not_contains "$(cat "$ST/sent.log")" 'please also merge the docs PR' 'the held text was never typed into the lane'
assert_contains "${sent[0]}" 'ready to recycle' 'the first thing typed is the hand-off request, not the text'
case "${sent[${#sent[@]}-1]}" in *"then $PEND"*"delete that file"*) ok ;; *) fail "the re-prime sends the lane to hub-pending.md: ${sent[${#sent[@]}-1]}" ;; esac
case "${sent[${#sent[@]}-1]}" in *"Read $HAND/prompt.md, then $HAND/handoff.md, then $PEND"*) ok ;; *) fail "read order is prompt, handoff, pending" ;; esac

# A second --send while that recycle is under way (its lock is held) is held too, and starts no second recycle.
shreset 60000
mkdir "$HOME_T/.claude/state/lanes/.recycle-sh-lane.lock"
lane --send sh-lane 'second thought'
assert_exit 0 "$LAST_RC" '--send during a recycle exits 0'
assert_contains "$LAST_OUT" 'a recycle is already under way' '--send says why it held the text'
assert_contains "$(cat "$PEND")" 'second thought' 'the text waits in hub-pending.md'
[ ! -s "$ST/sent.log" ] || fail "nothing is typed during a recycle: $(cat "$ST/sent.log")"; ok
sleep 1; [ ! -s "$ST/sent.log" ] || fail 'a second recycle was started'; ok
rmdir "$HOME_T/.claude/state/lanes/.recycle-sh-lane.lock"
# Two held messages are both kept, in order.
lane --send sh-lane 'third thought'
rmdir "$HOME_T/.claude/state/lanes/.recycle-sh-lane.lock" 2>/dev/null || true
[ "$(grep -c '^## ' "$PEND")" -ge 1 ] || fail 'hub-pending.md has no entry'; ok

# Under its line, a lane is typed into as always: no file, no recycle.
shreset 60000
lane --send sh-lane 'a normal message'
assert_exit 0 "$LAST_RC" '--send to a lane under its line exits 0'
assert_contains "$(cat "$ST/sent.log")" 'a normal message' 'a lane under its line is typed into'
[ ! -e "$PEND" ] || fail 'no hub-pending.md for a lane under its line'; ok
assert_not_contains "$LAST_OUT" 'held for' 'nothing is held'

# A lane recycled 3 times in the last 30 minutes is not recycled again by --send; the text is still held, and the output says so.
shreset 130000
for m in 25 15 5; do printf '%s\tsh-lane\trecycled\tfrom 120K (session a) to session b, line 128K\n' "$(date -Is -d "-$m min")" >>"$HOME_T/.claude/state/lanes/recycle.log"; done
lane --send sh-lane 'is anyone there'
assert_exit 0 "$LAST_RC" '--send to a thrashing lane exits 0'
assert_contains "$LAST_OUT" 'recycled 3 times in the last 30 minutes' '--send says it did not start another recycle'
assert_contains "$(cat "$PEND")" 'is anyone there' 'the text is in hub-pending.md'
sleep 1; [ ! -s "$ST/sent.log" ] || fail "a thrashing lane is not recycled by --send: $(cat "$ST/sent.log")"; ok

# A lane whose context cannot be read (no transcript yet) is typed into as before.
shreset 130000; rm -f "$PROJ/old-sess.jsonl"
lane --send sh-lane 'blind send'
assert_contains "$(cat "$ST/sent.log")" 'blind send' 'an unreadable context is typed into as before'
[ ! -e "$PEND" ] || fail 'no hub-pending.md when the context is unknown'; ok

# --recycle with a hub-pending.md that is not there leaves the re-prime exactly as before, and --dry-run mentions the file.
shreset 130000
lane --dry-run --recycle sh-lane
assert_contains "$LAST_OUT" 'hub-pending.md' '--recycle --dry-run says the re-prime reads held text'

printf 'ok - %s checks passed\n' "$checks"
