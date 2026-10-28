#!/usr/bin/env bash
# Tests for lane.sh's writes to index.tsv. 26 Oct: lane.sh rewrote the index with `cat "$tmp" >"$INDEX"` (truncate, then write) and no lock;
# a second lane.sh run read the file during the truncate, saw a prefix (364 of 982 rows) and wrote that prefix back, so every lane after
# row 364 lost its record. Now every write takes one lock, a rewrite replaces the file atomically, and a rewrite that has fewer rows than
# the file it replaces is refused and logged. A fake HOME holds the index: nothing here touches a live lane.
set -euo pipefail
# The CI machine has no claude, agy or codex program; the engine check (test_lane_engine_check.sh) is tested on its own.
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-index.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; INDEX="$LANES/index.tsv"
mkdir -p "$LANES"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

# Source just the index helpers out of lane.sh: from the INDEX= line to the TAB= line, with HOME pointed at the fake home.
export HOME="$HOME_T"
eval "$(sed -n '/^INDEX="\$HOME/,/^TAB=/p' "$SCRIPT")"
eval "$(sed -n '/^index_set_locked() {/,/^index_set() /p' "$SCRIPT")"
eval "$(sed -n '/^follow_session_locked() {/,/^}/p' "$SCRIPT")"

mkrow() { printf '2026-10-26\t%s\t/tmp/x\tsonnet\tsubscription\t/tmp/p.md\t/tmp/r.md\tid%s\t1700000000\thigh\toff\t100\t-\n' "$1" "$1"; }
for i in $(seq 1 300); do mkrow "lane$i" >>"$INDEX"; done

# 1. Concurrent index_set rewrites and appends lose no row and tear no line.
for w in 1 2 3 4; do
  ( for i in $(seq 1 15); do index_set "lane$((w * 20))" 12 "$((100 + i))"; done ) &
done
for w in 1 2 3; do
  ( for i in $(seq 1 15); do index_append "$(mkrow "new$w-$i")"; done ) &
done
wait
rows="$(wc -l <"$INDEX")"
[ "$rows" = 345 ] || fail "300 rows plus 45 appended expected, got $rows"; ok
bad="$(awk -F'\t' 'NF!=13' "$INDEX" | wc -l)"
[ "$bad" = 0 ] || fail "every row keeps its 13 fields, $bad do not"; ok
[ "$(grep -c '^2026-10-26	new' "$INDEX")" = 45 ] || fail "all 45 appended rows survive the rewrites"; ok
[ "$(awk -F'\t' '$2=="lane20"{print $12}' "$INDEX")" = 115 ] || fail "the last index_set write lands"; ok

# 2. A reader never sees a partial file while a rewrite runs.
( for i in $(seq 1 40); do index_set lane7 12 "$i"; done ) &
writer=$!
torn=0
while kill -0 "$writer" 2>/dev/null; do
  n="$(wc -l <"$INDEX")"; [ "$n" -ge 345 ] || torn=1
done
wait
[ "$torn" = 0 ] || fail "a reader saw fewer rows than the index ever held"; ok

# 3. A rewrite with fewer rows is refused, logged, and leaves the file as it was.
cp "$INDEX" "$TMP/before.tsv"
short="$(index_tmp)"; head -n 10 "$INDEX" >"$short"
rc=0; index_locked index_commit "$short" 2>/dev/null || rc=$?
[ "$rc" != 0 ] || fail "a shrinking rewrite is refused"; ok
cmp -s "$INDEX" "$TMP/before.tsv" || fail "the index is untouched after a refused rewrite"; ok
grep -q 'refused a rewrite of index.tsv: 10 rows would replace 345' "$LANES/index-guard.log" || fail "the refusal is logged"; ok
[ ! -e "$short" ] || fail "the refused temp file is removed"; ok
[ -z "$(ls "$LANES" | grep 'index.tsv.tmp')" ] || fail "no temp file is left behind"; ok

# 4. A rewrite keeps the file's mode.
chmod 640 "$INDEX"; index_set lane3 12 77
[ "$(stat -c %a "$INDEX")" = 640 ] || fail "the index keeps its mode"; ok

# 5. The shipped lane.sh has no in-place truncating write of the index left.
! grep -nE '>"\$INDEX"' "$SCRIPT" | grep -v '>>' | grep -v '^.*#' >/dev/null || fail "lane.sh still truncates the index in place"; ok

printf 'ok: %d checks\n' "$checks"
