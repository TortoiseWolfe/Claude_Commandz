#!/usr/bin/env bash
# Issue 83: three tools read how full a lane's conversation is (lane.sh ctx_k via --ctx, lane_brief.py context_k, hooks/context-warn.py
# latest_context_size via --size). They must give the same K figure for the same transcript, or the watcher, the brief and the warning
# disagree about when a lane is full. One saved transcript is written here and fed to all three.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
export AFA_ENGINES_AVAILABLE="*"

HERE="$(cd "$(dirname "$0")" && pwd -P)"
SCRIPTS="$(cd "$HERE/.." && pwd -P)"
HOOK="$(cd "$HERE/../../hooks" && pwd -P)/context-warn.py"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ctx-agree.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
HOME_T="$TMP/home"; REPO="$TMP/repo"; HAND="$HOME_T/.claude/state/lanes/2026-11-01-agree"
INDEX="$HOME_T/.claude/state/lanes/index.tsv"
SLUG="$(sed 's/[^A-Za-z0-9]/-/g' <<<"$REPO")"; PROJ="$HOME_T/.claude/projects/$SLUG"
mkdir -p "$REPO" "$HAND" "$PROJ"
echo 'do the thing' >"$HAND/prompt.md"
printf '%s\t%s\t%s\tsonnet\t%s\t%s\t%s\t%s\t-\thigh\toff\n' "$(date +%F)" agree-lane "$REPO" subscription "$HAND/prompt.md" "$HAND/report.md" agree-sess >"$INDEX"
T="$PROJ/agree-sess.jsonl"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }

rec() {  # rec <input> <cache_read> <cache_create> [extra json fields]
  printf '{"type":"assistant"%s,"message":{"role":"assistant","model":"m","usage":{"input_tokens":%d,"cache_read_input_tokens":%d,"cache_creation_input_tokens":%d}}}\n' "${4:-}" "$1" "$2" "$3"
}

readers() {  # prints "<lane.sh> <lane_brief> <context-warn>" in K, "?" for no reading
  local a b c w
  a="$(HOME="$HOME_T" bash "$SCRIPTS/lane.sh" --ctx agree-lane 2>/dev/null)"
  b="$(python3 -I - "$SCRIPTS" "$T" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import lane_brief
from pathlib import Path
k = lane_brief.context_k(Path(sys.argv[2]))
print("?" if k is None else k)
PY
)"
  w="$(python3 -I "$HOOK" --size "$T" 2>/dev/null || echo '')"
  if [ -z "$w" ]; then c='?'; else c="$(( (w + 500) / 1000 ))"; fi
  echo "$a $b $c"
}

agree() {  # agree <label> <want K or ?>
  local got
  got="$(readers)"
  [ "$got" = "$2 $2 $2" ] || fail "$1: want '$2 $2 $2' (lane.sh, lane_brief, context-warn), got '$got'"
  checks=$((checks + 1))
}

# 1. Plain turns: the last assistant usage counts, all three token kinds summed (12 + 100000 + 24988 = 125000).
{ rec 10 20000 500; rec 12 100000 24988; } >"$T"
agree 'last assistant usage, three token kinds' 125

# 2. Non-assistant records after it (user text, a tool result, a queue line, a bare summary) change nothing.
{ rec 12 100000 24988
  printf '{"type":"user","message":{"role":"user","content":"hi"}}\n'
  printf '{"type":"queue-operation","operation":"enqueue"}\n'
  printf '{"type":"summary","summary":"s"}\n'
  printf 'not json at all\n'; } >"$T"
agree 'later non-assistant records and a broken line' 125

# 3. A later sidechain (subagent) turn is still an assistant record carrying usage: every reader takes it as the last reading.
{ rec 12 100000 24988; rec 5 40000 995 ',"isSidechain":true'; } >"$T"
agree 'a later sidechain turn' 41

# 3b. A later synthetic record (Claude Code writes one with zero usage for an interrupted or errored turn) is not a reading: all three
# skip it and keep the real turn before it. lane.sh and lane_brief read 0K here until 18 Nov (found by the issue 83 test).
{ rec 12 100000 24988; printf '{"type":"assistant","message":{"role":"assistant","model":"<synthetic>","usage":{"input_tokens":0,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n'; } >"$T"
agree 'a later synthetic record' 125

# 4. Rounding: 125500 tokens is 126K for round(), and 124499 is 124K.
{ rec 0 125000 500; } >"$T"
agree 'rounds half up at .5K' 126
{ rec 0 124000 499; } >"$T"
agree 'rounds down below .5K' 124

# 5. No usage anywhere: no reading, shown as ? by all three.
printf '{"type":"user","message":{"role":"user","content":"hi"}}\n' >"$T"
agree 'no assistant usage' '?'

# 6. A transcript longer than the 400 lines the shell and brief readers keep still reads its last turn.
{ for _ in $(seq 1 450); do printf '{"type":"user","message":{"role":"user","content":"x"}}\n'; done; rec 1 59000 0; } >"$T"
agree 'long transcript' 59

printf 'ok: %d checks, the three context readers agree\n' "$checks"
