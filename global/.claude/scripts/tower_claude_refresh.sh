#!/usr/bin/env bash
# Keep the tower's ~/.claude code folders level with upstream without anyone asking (1 Nov 2026).
# NX-01's ~/.claude is the source of truth and Claude_Commandz (CC) plus hub/claude-private are copies made from it, so a merged fix
# reaches NX-01 by itself. Any other machine pulls: this wraps `hub/bootstrap.sh --refresh` (atomic copies, never deletes, never touches
# settings.json) so that it runs at most once an hour from the keeper job (openclaw_status_refresh.sh), and keeps a copy of every file
# it replaces in ~/.claude/state/tower-refresh-backup/<stamp>/ so a tower-only edit is never lost.
# usage: tower_claude_refresh.sh [--force]     env: HUB_DIR (default ~/repos), REFRESH_EVERY (seconds, default 3600)
set -u
HUB_DIR="${HUB_DIR:-$HOME/repos}"
STATE="$HOME/.claude/state"; STAMP="$STATE/tower-refresh.stamp"; LOG="$STATE/tower-refresh.log"
EVERY="${REFRESH_EVERY:-3600}"
machine="$(tr -d ' \r\n' <"$HOME/.config/afa/machine" 2>/dev/null || true)"
[ "$machine" = NX-01 ] && exit 0                      # NX-01 is the source; it never pulls itself
mkdir -p "$STATE"
now=$(date +%s)
if [ "${1:-}" != --force ] && [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  [ $((now - last)) -lt "$EVERY" ] && exit 0
fi
echo "$now" >"$STAMP"
say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }
[ -x "$HUB_DIR/hub/bootstrap.sh" ] || { say "no $HUB_DIR/hub/bootstrap.sh; stopping"; exit 1; }
# The private half lives in the hub checkout: fast-forward it when it is clean enough (never rebase or stash here).
if git -C "$HUB_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$HUB_DIR" fetch -q origin main 2>>"$LOG" && git -C "$HUB_DIR" merge --ff-only -q origin/main 2>>"$LOG" \
    || say "hub checkout not fast-forwarded (local commits or edits); using what is there"
fi
plan="$(timeout 120 "$HUB_DIR/hub/bootstrap.sh" --refresh --dry-run 2>&1)" || { say "dry run failed: $(printf '%s' "$plan" | tail -n 2 | tr '\n' ' ')"; exit 1; }
backup="$STATE/tower-refresh-backup/$(date +%Y%m%d-%H%M%S)"
n=0
while IFS= read -r f; do
  [ -f "$f" ] || continue                             # a missing file has nothing to lose
  rel="${f#"$HOME/.claude/"}"; mkdir -p "$backup/$(dirname "$rel")"; cp -p -- "$f" "$backup/$rel" && n=$((n + 1))
done < <(printf '%s\n' "$plan" | sed -n 's/^would: install //p')
out="$(timeout 300 "$HUB_DIR/hub/bootstrap.sh" --refresh 2>&1)" || { say "refresh failed: $(printf '%s' "$out" | tail -n 2 | tr '\n' ' ')"; exit 1; }
say "$(printf '%s' "$out" | tail -n 1); $n replaced file(s) backed up to $backup"
