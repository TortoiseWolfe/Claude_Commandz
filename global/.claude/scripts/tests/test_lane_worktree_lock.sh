#!/usr/bin/env bash
# Tests for the worktree lock in lane.sh (6 Nov 2026: a tidy of merged branches ran `git worktree remove` on grand-daze--red-main one minute
# after grand-daze-red started in it, and the lane lost its working folder). A start or --resume in a linked worktree now runs `git worktree lock`
# with a reason naming the lane, so `git worktree remove` (even --force) and `git worktree prune` refuse; a main checkout is never locked; a lock
# somebody else made is left alone; `lane.sh --unlock-gone` frees the locks whose lane has no window and no pane inside the worktree.
# A fake HOME, a fake tmux and a throwaway git repo hold everything: nothing here touches a live lane or a real worktree.
set -euo pipefail
export AFA_ENGINES_AVAILABLE="*"

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-wtlock.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; LANES="$HOME_T/.claude/state/lanes"; MAIN="$HOME_T/repos/proj"; WT="$HOME_T/repos/.worktrees/proj--feat"
mkdir -p "$BIN" "$LANES" "$MAIN" "$HOME_T/repos/.worktrees"
cat >"$BIN/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  list-windows) [ -n "${FAKE_WINDOWS:-}" ] && printf '%s\n' $FAKE_WINDOWS; exit 0 ;;
  list-panes) [ -n "${FAKE_PANES:-}" ] && printf '%s\n' $FAKE_PANES; exit 0 ;;
  capture-pane) printf 'ready\n' ;;
  send-keys) [ "${5:-}" = Enter ] && printf "%s\n" "${4:-}" >>"$FAKE_KEYS" || true ;;
esac
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/sleep"
chmod +x "$BIN/tmux" "$BIN/sleep"
KEYS="$TMP/keys.log"; : >"$KEYS"
g() { git -c user.name=t -c user.email=t@x.invalid "$@"; }

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane() { set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:$PATH" FAKE_KEYS="$KEYS" FAKE_WINDOWS="${FAKE_WINDOWS:-}" FAKE_PANES="${FAKE_PANES:-}" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }
has() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2; got: $1)" ;; esac; }
hasnt() { case "$1" in *"$2"*) fail "$3 (unexpected: $2)" ;; *) ok ;; esac; }
locked() { git -C "$MAIN" worktree list --porcelain | awk -v w="$WT" '$1=="worktree"{cur=$2} $1=="locked"&&cur==w{f=1} END{exit !f}'; }

g -C "$MAIN" init -q -b main
printf 'x\n' >"$MAIN/f"; g -C "$MAIN" add f; g -C "$MAIN" commit -q -m one
g -C "$MAIN" worktree add -q -b feat "$WT"
printf 'Mission one.\n' >"$TMP/p1.md"

# 1. A dry run locks nothing.
lane --dry-run wtl "$WT" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a dry run works ($LAST_OUT)"; ok
locked && fail "a dry run must not lock the worktree"; ok

# 2. A start in the linked worktree locks it, naming the lane and the way out.
lane wtl "$WT" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start in a linked worktree works ($LAST_OUT)"; ok
locked || fail "the lane's worktree is locked after the start"; ok
reason="$(cat "$MAIN/.git/worktrees/proj--feat/locked")"
has "$reason" "lane.sh: lane wtl " "the reason names the lane"
has "$reason" "--unlock-gone" "the reason names the way out"

# 3. Remove (even forced) and prune refuse; the folder is still there.
set +e; out="$(g -C "$MAIN" worktree remove "$WT" 2>&1)"; rc=$?; set -e
[ "$rc" != 0 ] || fail "worktree remove must refuse a locked worktree"; ok
set +e; out2="$(g -C "$MAIN" worktree remove --force "$WT" 2>&1)"; rc=$?; set -e
[ "$rc" != 0 ] || fail "worktree remove --force alone must refuse a locked worktree"; ok
has "$out$out2" "lane.sh: lane wtl" "the refusal shows the lock reason"
g -C "$MAIN" worktree prune; [ -d "$WT" ] || fail "the folder is untouched"; ok

# 4. A main checkout is never locked.
lane wtm "$MAIN" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start in a main checkout works ($LAST_OUT)"; ok
[ ! -e "$MAIN/.git/locked" ] || fail "a main checkout is not locked"; ok
[ "$(git -C "$MAIN" worktree list --porcelain | grep -c '^locked')" = 1 ] || fail "only the linked worktree is locked"; ok

# 5. --unlock-gone keeps the lock while the lane has a window, or a pane inside the worktree.
FAKE_WINDOWS="wtl other"; FAKE_PANES=""
lane --unlock-gone
has "$LAST_OUT" "kept: $WT (lane wtl is running)" "a live window keeps the lock"
locked || fail "still locked while the window exists"; ok
FAKE_WINDOWS="other"; FAKE_PANES="$WT/sub"
lane --unlock-gone
has "$LAST_OUT" "kept:" "a pane inside the worktree keeps the lock"
locked || fail "still locked while a pane sits inside it"; ok

# 6. With no window and no pane: --dry-run says so and changes nothing; the real run unlocks.
FAKE_WINDOWS="other"; FAKE_PANES="$TMP"
lane --dry-run --unlock-gone
has "$LAST_OUT" "would unlock: $WT" "a dry run reports the unlock"
locked || fail "a dry run leaves the lock"; ok
lane --unlock-gone
has "$LAST_OUT" "unlocked: $WT" "the real run reports the unlock"
locked && fail "unlocked after the lane's window is gone"; ok
g -C "$MAIN" worktree remove "$WT" || fail "the hub can remove it once unlocked"; ok

# 7. A lock somebody else made is left alone, by the start and by --unlock-gone.
g -C "$MAIN" worktree add -q -b feat2 "$WT"
g -C "$MAIN" worktree lock --reason "hub: keep this" "$WT"
lane wtl2 "$WT" sonnet "$TMP/p1.md"
[ "$LAST_RC" = 0 ] || fail "a start in an already locked worktree works ($LAST_OUT)"; ok
[ "$(cat "$MAIN/.git/worktrees/proj--feat/locked")" = "hub: keep this" ] || fail "another lock's reason is not overwritten"; ok
FAKE_WINDOWS=""; FAKE_PANES=""
lane --unlock-gone
hasnt "$LAST_OUT" "unlocked" "a foreign lock is not freed"
locked || fail "the foreign lock stays"; ok

# 8. Extra arguments are refused.
lane --unlock-gone extra
[ "$LAST_RC" = 1 ] && has "$LAST_OUT" "usage: lane.sh [--dry-run] --unlock-gone" "--unlock-gone takes no argument"

echo "test_lane_worktree_lock: $checks checks passed"
