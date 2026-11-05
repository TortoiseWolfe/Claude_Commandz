#!/usr/bin/env bash
# The no-AI wall at the lane launcher (issue 228): every engine, claude and --console included, is refused when the folder, an --add-dir or the
# prompt file sits under a listed path, --allow-client does not override it, and --clean-room <scratch> is allowed only when the scratch folder and
# prompt file match nothing and the prompt text names no listed path. Fake folders, a fake HOME and a fake tmux; nothing live is touched.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"
SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-noai.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
TMP="$(cd "$TMP" && pwd -P)"
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"
WALL="$TMP/wall/clientA"; OPEN="$TMP/open"; SCRATCH="$TMP/scratch"
mkdir -p "$BIN" "$LANES" "$WALL" "$OPEN" "$SCRATCH" "$HOME_T/.config/afa"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) exit 0 ;;
  capture-pane) printf 'ready\n' ;;
  send-keys) [ "${5:-}" = Enter ] && printf "%s\n" "${4:-}" >>"$FAKE_KEYS" || true ;;
esac
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"
KEYS="$TMP/keys.log"; : >"$KEYS"
PF="$TMP/paths.txt"; printf '%s\n' "$WALL" >"$PF"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" NO_AI_PATHS_FILE="${LIST:-$PF}" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
refused() { [ "$LAST_RC" != 0 ] || fail "$1 (it started: $LAST_OUT)"; has "$LAST_OUT" "no-AI list" "$1 names the list"; }

printf 'Ordinary mission.\n' >"$TMP/p.md"
printf 'Work in %s please.\n' "$WALL" >"$TMP/p-names.md"
printf 'Prompt file that itself is listed.\n' >"$WALL/prompt.md"

# 1. The folder is listed: claude, --console and agy are all refused, under --dry-run too.
lane --dry-run w1 "$WALL" sonnet "$TMP/p.md"; refused "claude in a listed folder"
lane --dry-run --console w1 "$WALL" sonnet "$TMP/p.md"; refused "--console in a listed folder"
lane --dry-run --engine agy w1 "$WALL" sonnet "$TMP/p.md"; refused "agy in a listed folder"
lane --dry-run --engine codex w1 "$WALL" sonnet "$TMP/p.md"; refused "codex in a listed folder"
has "$LAST_OUT" "(entry 1)" "the refusal names the list entry by number only"

# 2. A folder under a listed one, --allow-client does not override.
mkdir -p "$WALL/sub"
lane --dry-run --allow-client w1 "$WALL/sub" sonnet "$TMP/p.md"; refused "a subfolder, with --allow-client"
lane --dry-run --allow-client --engine agy w1 "$WALL" sonnet "$TMP/p.md"; refused "agy with --allow-client"

# 3. --add-dir and the prompt file count too.
lane --dry-run --add-dir "$WALL" w2 "$OPEN" sonnet "$TMP/p.md"; refused "a listed --add-dir"
lane --dry-run w2 "$OPEN" sonnet "$WALL/prompt.md"; refused "a listed prompt file"

# 4. An ordinary folder starts, with the wall on.
lane --dry-run w3 "$OPEN" sonnet "$TMP/p.md"
[ "$LAST_RC" = 0 ] || fail "an unlisted folder starts ($LAST_OUT)"; ok

# 5. --clean-room: the lane works in the scratch folder, not in <dir>.
lane --dry-run --clean-room "$SCRATCH" w4 "$WALL" sonnet "$TMP/p.md"
[ "$LAST_RC" = 0 ] || fail "a clean room starts ($LAST_OUT)"; ok
has "$LAST_OUT" "clean room: the lane works in $SCRATCH" "the clean room says where the lane works"
lane --dry-run --clean-room "$WALL" w4 "$OPEN" sonnet "$TMP/p.md"; [ "$LAST_RC" != 0 ] || fail "a listed scratch folder is refused"; ok
lane --dry-run --clean-room "$SCRATCH" w4 "$OPEN" sonnet "$TMP/p-names.md"
[ "$LAST_RC" != 0 ] || fail "a prompt naming a listed path is refused in a clean room"; ok
has "$LAST_OUT" "prompt text names a path" "the prompt-text refusal says why"
lane --dry-run --clean-room "$SCRATCH" w4 "$OPEN" sonnet "$WALL/prompt.md"; [ "$LAST_RC" != 0 ] || fail "a listed prompt file is refused in a clean room"; ok
lane --dry-run --clean-room "$SCRATCH" --add-dir "$WALL" w4 "$OPEN" sonnet "$TMP/p.md"; [ "$LAST_RC" != 0 ] || fail "a listed --add-dir is refused in a clean room"; ok
lane --dry-run --clean-room "$TMP/nonexistent" w4 "$OPEN" sonnet "$TMP/p.md"; [ "$LAST_RC" != 0 ] || fail "a missing scratch folder is refused"; ok
lane --dry-run --clean-room w4 "$OPEN" sonnet "$TMP/p.md"; [ "$LAST_RC" != 0 ] || fail "--clean-room swallowing the lane name is not a start"; ok

# 6. A missing list means the wall is off; an unreadable one refuses everything.
LIST="$TMP/absent.txt" lane --dry-run w5 "$WALL" sonnet "$TMP/p.md"
[ "$LAST_RC" = 0 ] || fail "with no list the wall is off ($LAST_OUT)"; ok
printf 'not a path\n' >"$TMP/bad.txt"
LIST="$TMP/bad.txt" lane --dry-run w5 "$OPEN" sonnet "$TMP/p.md"; [ "$LAST_RC" != 0 ] || fail "an unreadable list refuses every start"; ok

# 7. --clean-room is for starting a lane only.
lane --dry-run --clean-room "$SCRATCH" --resume w3; [ "$LAST_RC" != 0 ] || fail "--clean-room with --resume is refused"; ok

# 8. The helper comes from the wall's own folder under HOME, by absolute path; the checkout's mirror is only the fallback, and with neither the launcher refuses.
mkdir -p "$HOME_T/.claude/no-ai-wall"
printf 'no_ai_wall_paths_hit() { return 0; }\nno_ai_wall_text_hit() { return 0; }\n' >"$HOME_T/.claude/no-ai-wall/no_ai_wall.sh"
lane --dry-run w6 "$OPEN" sonnet "$TMP/p.md"
[ "$LAST_RC" != 0 ] || fail "the helper in ~/.claude/no-ai-wall is the one that decides (a refuse-all stand-in did not refuse: $LAST_OUT)"; ok
rm -f "$HOME_T/.claude/no-ai-wall/no_ai_wall.sh"
lane --dry-run w6 "$OPEN" sonnet "$TMP/p.md"
[ "$LAST_RC" = 0 ] || fail "with no home copy the checkout's mirror is used ($LAST_OUT)"; ok
# a lane.sh copied away from its repo mirror, with no helper under HOME either: refused, never started without the wall
mkdir -p "$TMP/lonely/scripts"; cp "$SCRIPT" "$TMP/lonely/scripts/lane.sh"
set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" NO_AI_PATHS_FILE="$PF" bash "$TMP/lonely/scripts/lane.sh" --dry-run w7 "$OPEN" sonnet "$TMP/p.md" 2>&1)"; LAST_RC=$?; set -e
[ "$LAST_RC" != 0 ] || fail "with no helper anywhere the launcher must not start ($LAST_OUT)"; ok
has "$LAST_OUT" "no-ai-wall/no_ai_wall.sh is missing" "the refusal says which file is missing"

echo "all $checks checks passed"
