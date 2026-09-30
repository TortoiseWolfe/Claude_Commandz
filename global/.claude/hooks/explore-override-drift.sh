#!/usr/bin/env bash
#
# explore-override-drift.sh — say when the built-in Explore subagent has changed
# underneath our ~/.claude/agents/Explore.md override.
#
# WHY THIS EXISTS
#
# Built-in Explore is defined with `model: inherit`, which beats
# CLAUDE_CODE_SUBAGENT_MODEL, so on an Opus session every Explore search ran on
# Opus (seen 2026-09-30). The documented fix is a user agent named Explore, which
# overrides the built-in. Ours is a verbatim copy of the built-in prompt, tools and
# description from Claude Code 2.1.286 with only `model: sonnet` changed.
#
# A copy freezes. When a Claude Code update improves the built-in Explore, the
# override silently keeps the old one. This compares the installed binary's
# built-in Explore against the reference text saved when the copy was made.
#
# THE ONE RULE: "unchanged" and "could not check" must never look the same.
# Every failure path reports UNKNOWN; only a clean comparison stays silent.
#
# Modes:  session-start (silent unless drift/unknown) | check | diff | seed
# Not `set -e`: a hook that dies mid-way emits nothing, which reads as all clear.
set -uo pipefail
MODE="${1:-check}"
OVERRIDE="$HOME/.claude/agents/Explore.md"
REF="$HOME/.claude/hooks/explore-override-drift.ref"
START='You are a file search specialist for Claude Code'
END='report your findings clearly.'
DESC='Read-only search agent for broad fan-out searches'

emit() {
  if [ "$MODE" != "session-start" ]; then printf '%s\n' "$1"; return; fi
  # additionalContext must nest inside hookSpecificOutput or it is ignored.
  TEXT="$1" python3 -c 'import json,os; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":os.environ["TEXT"]}}))'
}

[ -f "$OVERRIDE" ] || exit 0   # no override, nothing to keep in step

BIN="${EXPLORE_DRIFT_BIN:-$(readlink -f "$(command -v claude 2>/dev/null)" 2>/dev/null)}"
VER=$(basename "${BIN:-unknown}")
if [ -z "${BIN:-}" ] || [ ! -f "$BIN" ]; then
  emit "explore-override-drift: UNKNOWN, could not locate the claude binary, so ~/.claude/agents/Explore.md was not checked against the built-in Explore."
  exit 0
fi

# Built-in prompt text, with minified ${...} placeholders normalised so a rebuild
# that only renames variables doesn't count as a change. The binary holds the
# prompt twice: once as bytecode (binary bytes spliced into the text) and once as
# JS source. Only the copy that decodes as clean UTF-8 source is used.
current() {
  python3 - "$BIN" "$START" "$END" <<'PY'
import mmap, re, sys
path, start, end = sys.argv[1], sys.argv[2].encode(), sys.argv[3].encode()
with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
    i = m.find(start)
    while i >= 0:
        j = m.find(end, i, i + 6000)
        if j >= 0:
            try:
                s = m[i:j + len(end)].decode("utf-8")
            except UnicodeDecodeError:
                s = None
            if s and "${" in s and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", s):
                # Placeholders can nest one level (${e?`...${r?"x":""}...`:"..."}).
                print(re.sub(r"\$\{(?:[^{}]|\{[^{}]*\})*\}", "${}", s))
                sys.exit(0)
        i = m.find(start, i + 1)
sys.exit(1)
PY
}

case "$MODE" in
  seed)
    current > "$REF" && echo "seeded $REF from $VER" || { echo "seed failed: built-in Explore prompt not found in $VER"; exit 1; } ;;
  diff)
    diff -u "$REF" <(current) && echo "no change since the reference" ;;
  *)
    if [ ! -f "$REF" ]; then
      emit "explore-override-drift: UNKNOWN, reference $REF is missing; run: bash ~/.claude/hooks/explore-override-drift.sh seed"
      exit 0
    fi
    if ! NOW=$(current); then
      emit "explore-override-drift: Claude Code $VER no longer contains the built-in Explore prompt this check looks for, so ~/.claude/agents/Explore.md (a copy from 2.1.286 that pins Explore to Sonnet) may be stale. Compare it with the built-in's definition, keep model: sonnet, and re-seed."
      exit 0
    fi
    changed=""
    [ "$NOW" = "$(cat "$REF")" ] || changed="prompt"
    LC_ALL=C grep -a -q -F "$DESC" "$BIN" || changed="${changed:+$changed and }description"
    if [ -n "$changed" ]; then
      emit "explore-override-drift: the built-in Explore $changed changed in Claude Code $VER. ~/.claude/agents/Explore.md is a copy from 2.1.286 that pins Explore to Sonnet. Run \`bash ~/.claude/hooks/explore-override-drift.sh diff\`, carry the changes into Explore.md keeping model: sonnet, then re-seed."
    elif [ "$MODE" = "check" ]; then
      echo "explore-override-drift: built-in Explore unchanged in $VER"
    fi ;;
esac
exit 0
