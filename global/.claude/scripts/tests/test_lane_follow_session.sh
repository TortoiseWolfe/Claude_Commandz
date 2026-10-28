#!/usr/bin/env bash
# Tests for lane.sh following a lane's live session (22 Oct 7:20 AM: harmonicegg-mail's index row said da6e22ef while the live session was
# 00e024a9, so --ctx read 22K while the lane sat at 157K and lane_watch never recycled it). A /clear or a --resume starts a new transcript
# and nothing rewrote column 8 of the row. --ctx, --turn and --resume now take the session the lane's own SessionStart hook recorded
# (lane_session_hook.sh) and write it back. 23 Oct: the first version guessed from file age, and a lane in ~/repos was handed the hub's own
# session; a session that no lane window recorded is never a successor. A fake HOME holds the index, the records and the transcripts:
# nothing here touches a live lane.
set -euo pipefail
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"
# A lane running this test carries its own AFA_LANE_NAME, which made the hook write a session file under the real lane's name here.
unset AFA_LANE_NAME

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-follow.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
HOME_T="$TMP/home"; REPO="$TMP/repo"; LANES="$HOME_T/.claude/state/lanes"; INDEX="$LANES/index.tsv"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$REPO" "$LANES" "$PROJ"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

lane() { set +e; LAST_OUT="$(HOME="$HOME_T" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
transcript() {  # transcript <id> <context tokens> <age in seconds>: a one-turn session whose last usage is <tokens>
  local f="$PROJ/$1.jsonl"
  printf '{"type":"assistant","message":{"role":"assistant","usage":{"input_tokens":1000,"cache_read_input_tokens":%d,"cache_creation_input_tokens":0}}}\n' $(( $2 - 1000 )) >"$f"
  touch -d "@$(( $(date +%s) - $3 ))" "$f"
}
row() {  # row <name> <session id>
  printf '%s\t%s\t%s\tsonnet\tsubscription\t%s\t%s\t%s\t%s\thigh\toff\t100\n' "$(date +%F)" "$1" "$REPO" "$TMP/p.md" "$TMP/$1-report.md" "$2" "$(( $(date +%s) - 5000 ))" >>"$INDEX"
}
id_of() { awk -F'\t' -v n="$1" '$2==n{i=$8} END{print i}' "$INDEX"; }
: >"$TMP/p.md"

SESS="$LANES/sessions"; mkdir -p "$SESS"
HOOK="$(dirname "$SCRIPT")/lane_session_hook.sh"
mkdir -p "$TMP/bin"; export FAKE_WIN="$TMP/fake-win"
cat >"$TMP/bin/tmux" <<'SH'
#!/usr/bin/env bash
# fake tmux: "display -p -t <pane> #{window_name}" answers with the contents of $FAKE_WIN (nothing when the file is absent, as for a dead pane)
[ "$1" = display ] && [ -f "$FAKE_WIN" ] && cat "$FAKE_WIN"
exit 0
SH
chmod +x "$TMP/bin/tmux"
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$TMP/bin:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
record() {  # record <lane> <session id> [pane]: what the lane's SessionStart hook writes
  printf '%s\t%s\t%s\n' "$2" "$(date +%s)" "${3:--}" >>"$SESS/$1"
}

# A lane nobody cleared stays on its own session.
row steady aaaa1111
transcript aaaa1111 40000 60
lane --ctx steady
[ "$LAST_OUT" = 40 ] || fail "--ctx reads the row's own session, got: $LAST_OUT"; ok
[ "$(id_of steady)" = aaaa1111 ] || fail "an unchanged lane keeps its id, got: $(id_of steady)"; ok

# 23 Oct: a session in the same folder that no lane window recorded (the hub's own, a person's) is NOT the lane's successor, however new.
# This is the normal case in ~/repos.
sleep 1.1
transcript hubsess9 250000 2
lane --ctx steady
[ "$LAST_OUT" = 40 ] || fail "a non-tmux session in the lane's folder is not followed, got: $LAST_OUT"; ok
[ "$(id_of steady)" = aaaa1111 ] || fail "the row keeps the lane's own session, got: $(id_of steady)"; ok
lane --dry-run --resume steady
case "$LAST_OUT" in *"session aaaa1111"*) ok ;; *) fail "--resume does not take the hub's session, got: $LAST_OUT" ;; esac

# A /clear in the lane's window: its hook (the real script) records bbbb2222. --ctx follows it and writes it back to the row.
transcript bbbb2222 157000 10
printf '{"session_id":"bbbb2222","source":"clear"}' | AFA_LANE_NAME=steady HOME="$HOME_T" TMUX_PANE=%7 bash "$HOOK"
[ "$(cut -f1 "$SESS/steady")" = bbbb2222 ] && [ "$(cut -f3 "$SESS/steady")" = %7 ] || fail "the hook records session and pane, got: $(cat "$SESS/steady")"; ok
lane --ctx steady
[ "$LAST_OUT" = 157 ] || fail "--ctx follows the lane's recorded session, got: $LAST_OUT"; ok
[ "$(id_of steady)" = bbbb2222 ] || fail "the index row now holds the live session, got: $(id_of steady)"; ok
[ "$(awk -F'\t' '$2=="steady"{print NF}' "$INDEX")" -ge 12 ] || fail 'the rewritten row keeps its columns'; ok
lane --ctx steady
[ "$LAST_OUT" = 157 ] || fail "a second read stays on the live session, got: $LAST_OUT"; ok

# The hook writes only for a lane, only a valid id, and not the same id twice.
printf '{"session_id":"cccc9999"}' | HOME="$HOME_T" bash "$HOOK"
printf '{"session_id":"not an id"}' | AFA_LANE_NAME=steady HOME="$HOME_T" bash "$HOOK"
printf '{"session_id":"bbbb2222"}' | AFA_LANE_NAME=steady HOME="$HOME_T" bash "$HOOK"
printf '{"session_id":"cccc9999"}' | AFA_LANE_NAME='../evil' HOME="$HOME_T" bash "$HOOK"
[ "$(ls "$SESS")" = steady ] && [ "$(wc -l <"$SESS/steady")" = 1 ] || fail "hook wrote where it should not: $(ls "$SESS") / $(wc -l <"$SESS/steady") lines"; ok

# Only the lane's LAST row moves: an older row of the same name and every other lane's row keep their ids.
row multi old11111
row other cccc3333
row multi old22222
transcript old11111 10000 9000
transcript old22222 20000 9000
transcript dddd4444 90000 10
record multi dddd4444
lane --ctx multi
[ "$LAST_OUT" = 90 ] || fail "the lane's later row follows its recorded session, got: $LAST_OUT"; ok
[ "$(awk -F'\t' '$2=="multi"{i[NR]=$8} END{for (k in i) printf "%s ", i[k]}' "$INDEX" | tr ' ' '\n' | sort | tr '\n' ' ')" = "dddd4444 old11111 " ] \
  || fail "only the last row of the name was rewritten, got: $(awk -F'\t' '$2=="multi"{print $8}' "$INDEX" | tr '\n' ' ')"; ok
[ "$(id_of other)" = cccc3333 ] || fail "another lane's row is untouched, got: $(id_of other)"; ok

# A session another lane's row claims is not stolen, even if this lane's record names it.
row owner eeee5555
transcript eeee5555 30000 1
row claimer ffff6666
transcript ffff6666 20000 100
record claimer eeee5555
lane --ctx claimer
[ "$LAST_OUT" = 20 ] || fail "a session claimed by another lane is not taken, got: $LAST_OUT"; ok
[ "$(id_of claimer)" = ffff6666 ] || fail "the claiming lane keeps its id, got: $(id_of claimer)"; ok

# A record older than the row (left by an earlier run of the same name) is not this lane's.
row late hhhh8888
transcript hhhh8888 60000 100
transcript gggg7777 5000 1
printf '%s\t%s\t-\n' gggg7777 "$(( $(date +%s) - 9000 ))" >>"$SESS/late"
lane --ctx late
[ "$LAST_OUT" = 60 ] || fail "a record from before the row is not followed, got: $LAST_OUT"; ok
[ "$(id_of late)" = hhhh8888 ] || fail "the lane keeps its id, got: $(id_of late)"; ok

# A record naming a pane that is another window's is not followed; the lane's own window is.
row panes pppp0001
transcript pppp0001 11000 100
transcript pppp0002 77000 5
record panes pppp0002 %9
echo someone-else >"$FAKE_WIN"
lane --ctx panes
[ "$LAST_OUT" = 11 ] || fail "a record from another window's pane is not followed, got: $LAST_OUT"; ok
echo panes >"$FAKE_WIN"
lane --ctx panes
[ "$LAST_OUT" = 77 ] || fail "a record from the lane's own pane is followed, got: $LAST_OUT"; ok
rm -f "$FAKE_WIN"

# A record whose transcript does not exist yet is not followed (the row would point at nothing).
row early qqqq0001
transcript qqqq0001 12000 100
record early qqqq0002
lane --ctx early
[ "$LAST_OUT" = 12 ] && [ "$(id_of early)" = qqqq0001 ] || fail "a recorded session with no transcript yet is not followed, got: $LAST_OUT / $(id_of early)"; ok

# --resume resumes the live session, not the stale one the row remembers.
row res stale999
transcript stale999 10000 500
transcript live0000 80000 5
record res live0000
lane --dry-run --resume res
[ "$LAST_RC" = 0 ] || fail "dry-run resume works, got $LAST_RC: $LAST_OUT"; ok
case "$LAST_OUT" in *"session live0000"*) ok ;; *) fail "--resume uses the live session, got: $LAST_OUT" ;; esac

# A lane just started has "-" for its id: --ctx takes the session its hook recorded, not the newest transcript in the folder.
printf '%s\tnewlane\t%s\tsonnet\tsubscription\t%s\t%s\t-\t%s\thigh\toff\t100\n' "$(date +%F)" "$REPO" "$TMP/p.md" "$TMP/n-report.md" "$(( $(date +%s) - 20 ))" >>"$INDEX"
transcript hubnew00 200000 1
lane --ctx newlane
[ "$LAST_OUT" = '?' ] || fail "a new lane with no record does not pick up the newest transcript, got: $LAST_OUT"; ok
transcript nl000001 33000 5
record newlane nl000001
lane --ctx newlane
[ "$LAST_OUT" = 33 ] || fail "a new lane takes the session its hook recorded, got: $LAST_OUT"; ok

printf 'ok - %s checks passed\n' "$checks"
