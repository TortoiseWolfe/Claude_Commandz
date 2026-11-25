#!/usr/bin/env bash
# Tests that a lane start names the tools we already have (11 Nov 2026: a lane wrote its own database helper because nothing told it which tools exist).
# lane.sh appends the output of tools_catalog.py match to the prompt copy the lane reads, once. A dry run writes nothing and prints the section.
# Nothing here touches a live lane: the tmux on PATH is a stub and HOME is a temp folder.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-tools.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; WORK="$HOME_T/repos/proj"; LANES="$HOME_T/.claude/state/lanes"
mkdir -p "$BIN" "$WORK" "$LANES" "$HOME_T/.claude/scripts"
printf '#!/usr/bin/env bash\ncase "$1" in has-session) exit 0 ;; esac\nexit 0\n' >"$BIN/tmux"; chmod +x "$BIN/tmux"
# the matcher reads the catalog of its HOME: a small one with the safety tool in it
printf '# Our own command-line tools\n\n## Safety tools (use these first)\n\n- %s/.claude/scripts/redact_view.py | Print a config file with secrets masked.\n\n## All other tools (sorted by path)\n\n' "$HOME_T" >"$HOME_T/.claude/scripts/TOOLS.md"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

HEAD='## Tools you already have for this job'
P="$TMP/task.md"
printf 'Fix the config reader and report.\n' >"$P"

# 1. A dry run shows the section it would append, names redact_view.py, and creates no lane folder.
lane --dry-run tools-a "$WORK" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a dry run starts ($LAST_OUT)"; ok
has "$LAST_OUT" "$HEAD" "the dry run shows the heading"
has "$LAST_OUT" "redact_view.py" "the dry run names redact_view.py"
[ ! -e "$LANES/$(date +%F)-tools-a" ] || fail "a dry run made a lane folder"; ok
hasnt "$(cat -- "$P")" "$HEAD" "a dry run leaves the source prompt alone"

# 2. A prompt that already holds the heading is not given a second one (dry run prints no section).
printf 'Fix the config reader.\n\n%s\n\n- old line\n' "$HEAD" >"$TMP/task2.md"
lane --dry-run tools-b "$WORK" sonnet "$TMP/task2.md"
[ "$LAST_RC" = 0 ] || fail "a dry run with the heading starts ($LAST_OUT)"; ok
hasnt "$LAST_OUT" "from tools_catalog.py match" "no second section is offered when the heading is there"

# 2b. A real start (tmux is a stub) appends the section to the lane's copy once; a second start with the same text adds none and stores no note.
lane tools-c "$WORK" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a real start works ($LAST_OUT)"; ok
D="$LANES/$(date +%F)-tools-c"
[ "$(grep -c "^$HEAD" "$D/prompt.md")" = 1 ] || fail "the lane's prompt.md holds the heading once"; ok
has "$(cat -- "$D/prompt.md")" "redact_view.py" "the lane's prompt.md names redact_view.py"
hasnt "$(cat -- "$P")" "$HEAD" "the source prompt is not edited"
lane tools-c "$WORK" sonnet "$P"
[ "$(grep -c "^$HEAD" "$D/prompt.md")" = 1 ] || fail "a second start adds no second heading"; ok
[ -z "$(ls "$D"/prompt-*.md 2>/dev/null)" ] || fail "a second start of the same text stores no restart note"; ok

# 3. The real append is guarded by the same heading check, and a matcher failure cannot stop the start.
grep -q "grep -qF -- '$HEAD' \"\$lprompt\"" "$SCRIPT" || fail "the real append checks for the heading first"; ok
grep -q 'timeout 20 python3 -I "$SCRIPT_DIR/tools_catalog.py" match "$lprompt"' "$SCRIPT" || fail "the matcher runs under timeout 20"; ok
grep -q 'tools_sec=""' "$SCRIPT" || fail "a matcher failure is ignored"; ok

# 4. A repo with no graphify graph: a fresh start also puts a "## Graph" section in prompt.md, once; a dry run, a skipped repo and a repo with a graph get none.
GH='## Graph'
GREPO="$HOME_T/repos/widgets"; mkdir -p "$GREPO"; git -C "$GREPO" init -q
lane --dry-run graph-a "$GREPO" sonnet "$P"
[ ! -e "$LANES/$(date +%F)-graph-a/prompt.md" ] || fail "a dry run wrote a prompt.md"; ok
lane graph-b "$GREPO" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a start in a repo without a graph works ($LAST_OUT)"; ok
D="$LANES/$(date +%F)-graph-b"
[ "$(grep -c "^$GH\$" "$D/prompt.md")" = 1 ] || fail "prompt.md holds the Graph heading once"; ok
has "$(cat -- "$D/prompt.md")" "run /graphify-init" "the Graph section names /graphify-init"
hasnt "$(cat -- "$P")" "$GH" "the source prompt is not edited by the graph note"
lane graph-b "$GREPO" sonnet "$P"
[ "$(grep -c "^$GH\$" "$D/prompt.md")" = 1 ] || fail "a second start adds no second Graph heading"; ok
lane --restart graph-b "$GREPO" sonnet "$P"
[ "$LAST_RC" = 0 ] || fail "a restart works ($LAST_OUT)"; ok
[ "$(grep -c "^$GH\$" "$D/prompt.md")" -le 1 ] || fail "a restart adds no second Graph heading"; ok
mkdir -p "$HOME_T/.config/afa"; printf '%s\n' "$(cd "$GREPO" && pwd -P)" >"$HOME_T/.config/afa/graphify-skip"
lane graph-c "$GREPO" sonnet "$P"
hasnt "$(cat -- "$LANES/$(date +%F)-graph-c/prompt.md")" "$GH" "a repo on the skip list gets no Graph section"
rm -f "$HOME_T/.config/afa/graphify-skip"; mkdir -p "$GREPO/graphify-out"
lane graph-d "$GREPO" sonnet "$P"
hasnt "$(cat -- "$LANES/$(date +%F)-graph-d/prompt.md")" "$GH" "a repo with a graph gets no Graph section"

echo "ok - $checks checks passed"
