#!/usr/bin/env bash
# Tests for lane.sh --rules (CC issue 37): the start command names the rule parts a lane loads; keywords only warn. A fake HOME, a fake
# tmux and made-up rule parts hold everything. The dry run writes dry-rules-<name>.md under the fake HOME's state/out and starts nothing.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-rules-opt.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; WORK="$HOME_T/repos/proj"; RD="$TMP/rules"
mkdir -p "$BIN" "$WORK" "$LANES" "$RD"
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
for p in core mail browser streams coop fleet; do printf '## %s\n\n- rule text of %s\n' "$p" "$p" >"$RD/$p.md"; done

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" LANE_RULES_DIR="$RD" LANE_RULES="$TMP/pointer.md" LANE_RULES_KEYWORDS="$TMP/none.json" HUB_ROUTES_FILE="$TMP/none.md" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
printf "pointer\n" >"$TMP/pointer.md"
OUT="$HOME_T/.claude/state/out"
printf 'Fix the parser.\n' >"$TMP/p1.md"

# 1. --rules coop: the first line lists core and coop only.
lane --dry-run --rules coop rl1 "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a dry run with --rules works ($LAST_OUT)"; ok
first="$(head -n 1 "$OUT/dry-rules-rl1.md")"
has "$first" "parts loaded: core, coop;" "the first line lists core and coop"
grep -q "rule text of coop" "$OUT/dry-rules-rl1.md" || fail "coop text is in the file"; ok
! grep -q "rule text of mail" "$OUT/dry-rules-rl1.md" || fail "mail text is not in the file"; ok

# 2. A bogus part is refused before anything starts.
before="$(ls "$LANES" | wc -l)"
lane --rules bogus rl2 "$WORK" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 2 ] || fail "bogus part exits 2 (got $LAST_RC: $LAST_OUT)"; ok
has "$LAST_OUT" "unknown rule part bogus" "the refusal names the part"
[ "$(ls "$LANES" | wc -l)" = "$before" ] || fail "nothing was started"; ok
[ ! -s "$KEYS" ] || fail "no keys were typed"; ok
[ ! -e "$OUT/dry-rules-rl2.md" ] || fail "no rule file written"; ok

# 3. No option: today's keyword picking (a plain prompt gets core only).
lane --dry-run rl3 "$WORK" sonnet "$TMP/p1.md"
has "$(head -n 1 "$OUT/dry-rules-rl3.md")" "parts loaded: core;" "no option keeps the keyword picking"

echo "ok: $checks checks"
