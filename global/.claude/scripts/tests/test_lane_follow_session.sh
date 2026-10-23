#!/usr/bin/env bash
# Tests for lane.sh following a lane's live session (22 Oct 7:20 AM: harmonicegg-mail's index row said da6e22ef while the live session was
# 00e024a9, so --ctx read 22K while the lane sat at 157K and lane_watch never recycled it). A /clear or a --resume starts a new transcript
# and nothing rewrote column 8 of the row. --ctx, --turn and --resume now take the newest unclaimed transcript born no earlier than the
# row's own and written after it, and write it back. A fake HOME holds the index and the transcripts: nothing here touches a live lane.
set -euo pipefail

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

# A lane nobody cleared stays on its own session.
row steady aaaa1111
transcript aaaa1111 40000 60
lane --ctx steady
[ "$LAST_OUT" = 40 ] || fail "--ctx reads the row's own session, got: $LAST_OUT"; ok
[ "$(id_of steady)" = aaaa1111 ] || fail "an unchanged lane keeps its id, got: $(id_of steady)"; ok

# A /clear starts bbbb2222: --ctx follows it and writes it back to the lane's row.
sleep 1.1
transcript bbbb2222 157000 10
lane --ctx steady
[ "$LAST_OUT" = 157 ] || fail "--ctx follows the lane's new session, got: $LAST_OUT"; ok
[ "$(id_of steady)" = bbbb2222 ] || fail "the index row now holds the live session, got: $(id_of steady)"; ok
[ "$(awk -F'\t' '$2=="steady"{print NF}' "$INDEX")" = 12 ] || fail 'the rewritten row keeps its 12 columns'; ok
lane --ctx steady
[ "$LAST_OUT" = 157 ] || fail "a second read stays on the live session, got: $LAST_OUT"; ok

# Only the lane's LAST row moves: an older row of the same name and every other lane's row keep their ids.
row multi old11111
row other cccc3333
row multi old22222
transcript old11111 10000 9000
transcript old22222 20000 9000
sleep 1.1
transcript dddd4444 90000 10
lane --ctx multi
[ "$LAST_OUT" = 90 ] || fail "the lane's later row follows its new session, got: $LAST_OUT"; ok
[ "$(awk -F'\t' '$2=="multi"{i[NR]=$8} END{for (k in i) printf "%s ", i[k]}' "$INDEX" | tr ' ' '\n' | sort | tr '\n' ' ')" = "dddd4444 old11111 " ] \
  || fail "only the last row of the name was rewritten, got: $(awk -F'\t' '$2=="multi"{print $8}' "$INDEX" | tr '\n' ' ')"; ok
[ "$(id_of other)" = cccc3333 ] || fail "another lane's row is untouched, got: $(id_of other)"; ok

# A transcript another lane's row claims is not stolen.
row owner eeee5555
transcript eeee5555 30000 1
row claimer ffff6666
transcript ffff6666 20000 100
lane --ctx claimer
[ "$LAST_OUT" = 20 ] || fail "a transcript claimed by another lane is not taken, got: $LAST_OUT"; ok
[ "$(id_of claimer)" = ffff6666 ] || fail "the claiming lane keeps its id, got: $(id_of claimer)"; ok

# A transcript born before the row's own session is not its successor, however recently it was written.
transcript gggg7777 5000 4000
sleep 1.1
row late hhhh8888
transcript hhhh8888 60000 100
transcript gggg7777 5000 1   # written after hhhh8888, but the file was created before it: an older conversation
[ "$(stat -c %W "$PROJ/gggg7777.jsonl")" -lt "$(stat -c %W "$PROJ/hhhh8888.jsonl")" ] || fail 'fixture: gggg7777 must be the older file'; ok
lane --ctx late
[ "$LAST_OUT" = 60 ] || fail "an older transcript is not followed, got: $LAST_OUT"; ok
[ "$(id_of late)" = hhhh8888 ] || fail "the lane keeps its id when only an older transcript is newer, got: $(id_of late)"; ok

# --resume resumes the live session, not the stale one the row remembers.
row res stale999
transcript stale999 10000 500
sleep 1.1
transcript live0000 80000 5
lane --dry-run --resume res
[ "$LAST_RC" = 0 ] || fail "dry-run resume works, got $LAST_RC: $LAST_OUT"; ok
case "$LAST_OUT" in *"session live0000"*) ok ;; *) fail "--resume uses the live session, got: $LAST_OUT" ;; esac

printf 'ok - %s checks passed\n' "$checks"
