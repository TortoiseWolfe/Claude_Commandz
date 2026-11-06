#!/usr/bin/env bash
# Install the no-AI wall (issue 228) from one named commit. Run it in your own terminal, never from a model session: the wall refuses
# a session's changes to its own folder on purpose, so that no session can loosen its own guard.
#
#   bash <(git -C ~/repos/Claude_Commandz show <commit>:global/.claude/no-ai-wall/install.sh) <commit>
#
# By default it moves the wall and nothing else (steps 1 to 5 and 7). Step 6 copies every other stale global file at once, which is how
# lane.sh was broken on 6 Nov 2026, so it runs only when asked: put --with-tooling before the commit.
#
# (Running the script out of the commit it installs means the file you run is the file that was reviewed.)
#
# What it does, in order. Nothing touches the network or the working tree: every file comes out of the commit with git archive.
#   1. checks the commit has the wall's files and that they compile
#   2. backs up every live file it is about to replace into ~/.claude/state/no-ai-wall-backups/<date>-<commit>/ and writes undo.sh beside it
#   3. installs the wall's three files (module, bash helper, hook) into ~/.claude/no-ai-wall/, a folder that holds only those
#   4. moves the hook entry in settings.json to the new folder (a text substitution of the path; every other byte stays)
#   5. takes the three old copies out of ~/.claude/scripts and ~/.claude/hooks
#   6. ONLY WITH --with-tooling: installs the other global files the repo's usual install step installs (install_global.py: lane.sh and friends), refusing any
#      live edit it would overwrite, which it names and leaves alone (a live file equal to any earlier committed version is stale, not
#      edited, and is brought up to date)
#   7. runs smoke.py against the installed files, through the hook command settings.json registers
# The last line it prints is one of:
#   WALL OK <commit> refused <n>/<n> ordinary <n>/<n>
#   WALL FAILED <reason> after undoing itself          (it ran undo.sh; ~/.claude is back as it was)
# To undo a good install later: bash <the backup folder>/undo.sh
#
# Environment (for tests): WALL_INSTALL_REPO = the checkout to read commits from (default ~/repos/Claude_Commandz). HOME decides what
# "~/.claude" is. The private list ~/.config/afa/no-ai-paths.txt is read by the smoke test; nothing from it is printed.
set -uo pipefail
umask 022

REPO="${WALL_INSTALL_REPO:-${HOME:-/nonexistent}/repos/Claude_Commandz}"
LIVE="${HOME:-/nonexistent}/.claude"
WALL="$LIVE/no-ai-wall"
WALL_FILES=(no_ai_wall.py no_ai_wall.sh no-ai-wall-guard.py)
LEGACY=(hooks/no-ai-wall-guard.py scripts/no_ai_wall.py scripts/no_ai_wall.sh)
STAGE=""
BK=""

cleanup() { [ -n "$STAGE" ] && rm -rf "$STAGE"; return 0; }
trap cleanup EXIT

no_change() {  # a problem found before anything was touched
  echo "WALL FAILED $1; nothing had been changed yet"
  exit 1
}

fail_after() {  # a problem after the first change: run the undo, then say so
  local reason="$1"
  if [ -n "$BK" ] && [ -f "$BK/undo.sh" ]; then
    if bash "$BK/undo.sh" >/dev/null 2>&1; then
      echo "WALL FAILED $reason after undoing itself"
    else
      echo "WALL FAILED $reason; THE UNDO ALSO FAILED, run: bash $BK/undo.sh"
    fi
  else
    echo "WALL FAILED $reason; no backup had been written, so nothing was undone"
  fi
  exit 1
}

[ -n "${HOME:-}" ] || no_change "HOME is not set"
WITH_TOOLING=0
if [ "${1:-}" = "--with-tooling" ]; then WITH_TOOLING=1; shift; fi
[ $# -eq 1 ] || no_change "usage: install.sh [--with-tooling] <commit>"
command -v git >/dev/null && command -v tar >/dev/null && command -v python3 >/dev/null || no_change "git, tar and python3 are needed"
[ -e "$REPO/.git" ] || no_change "$REPO is not a git checkout"
SHA="$(git -C "$REPO" rev-parse --verify --quiet "$1^{commit}")" || no_change "unknown commit $1"
SHORT="${SHA:0:7}"

# 1. The commit's files, out of git (never the working tree), into a scratch folder.
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/wall-install.XXXXXX")" || no_change "cannot make a scratch folder"
git -C "$REPO" archive "$SHA" global/.claude | tar -x -C "$STAGE" 2>/dev/null || no_change "commit $SHORT has no global/.claude folder"
SRC="$STAGE/global/.claude"
for f in "${WALL_FILES[@]}" smoke.py; do
  [ -f "$SRC/no-ai-wall/$f" ] || no_change "commit $SHORT has no no-ai-wall/$f"
done
for f in no_ai_wall.py no-ai-wall-guard.py smoke.py; do
  python3 -I -c 'import sys; compile(open(sys.argv[1], "rb").read(), sys.argv[1], "exec")' "$SRC/no-ai-wall/$f" 2>/dev/null || no_change "no-ai-wall/$f does not compile"
done
bash -n "$SRC/no-ai-wall/no_ai_wall.sh" 2>/dev/null || no_change "no-ai-wall/no_ai_wall.sh has a syntax error"
[ -f "$LIVE/settings.json" ] || no_change "$LIVE/settings.json is missing"

# 2. Back up every live file this run could replace; undo.sh puts them back. The manifest is one `restore <path>` or `remove <path>`
# per line, paths relative to ~/.claude, in the order undo applies them (old wall files first, then the settings, then the rest), so the wall
# is never off in between.
TS="$(date +%Y%m%d-%H%M%S)"
BK="$LIVE/state/no-ai-wall-backups/$TS-$SHORT"
n=1; while [ -e "$BK" ]; do n=$((n + 1)); BK="$LIVE/state/no-ai-wall-backups/$TS-$SHORT-$n"; done
mkdir -p "$BK/files" || no_change "cannot make the backup folder $BK"
MANIFEST="$BK/manifest.tsv"
: >"$MANIFEST"

backup() {  # <path relative to ~/.claude>
  local rel="$1"
  if [ -e "$LIVE/$rel" ] || [ -L "$LIVE/$rel" ]; then
    mkdir -p "$BK/files/$(dirname "$rel")" && cp -a "$LIVE/$rel" "$BK/files/$rel" || return 1
    printf 'restore\t%s\n' "$rel" >>"$MANIFEST"
  else
    printf 'remove\t%s\n' "$rel" >>"$MANIFEST"
  fi
}

TOOLING_NOTE=""
TOOLING=()
if [ "$WITH_TOOLING" = 1 ]; then
  TOOLING_DRY="$(python3 -I "$SRC/scripts/install_global.py" --repo "$REPO" --home "$LIVE" --to "$SHA" --include-stale --dry-run 2>&1)"
  case $? in
    0|1) while IFS= read -r line; do
           case "$line" in "would copy  global/.claude/"*) TOOLING+=("${line#would copy  global/.claude/}") ;; esac
         done <<<"$TOOLING_DRY" ;;
    *) TOOLING_NOTE="the other global files were not installed ($(printf '%s' "$TOOLING_DRY" | tail -n 1 | cut -c1-160))" ;;
  esac
else
  TOOLING_NOTE="the other global files were not installed (only the wall was asked for; add --with-tooling before the commit to include them)"
fi

for rel in "${LEGACY[@]}"; do backup "$rel" || no_change "cannot back up $rel"; done
backup settings.json || no_change "cannot back up settings.json"
backup state/install-global-commit || no_change "cannot back up the last-install record"
for f in "${WALL_FILES[@]}"; do backup "no-ai-wall/$f" || no_change "cannot back up no-ai-wall/$f"; done
for rel in ${TOOLING[@]+"${TOOLING[@]}"}; do backup "$rel" || no_change "cannot back up $rel"; done

{
  echo '#!/usr/bin/env bash'
  echo "# Undo of the no-AI wall install of $SHA made $TS. Run it in your own terminal: bash $BK/undo.sh"
  echo 'set -u'
  printf 'LIVE=%q\nBK=%q\n' "$LIVE" "$BK"
  cat <<'UNDO'
rc=0
while IFS=$'\t' read -r act rel; do
  [ -n "$rel" ] || continue
  case "$act" in
    restore) mkdir -p "$(dirname "$LIVE/$rel")" && cp -a "$BK/files/$rel" "$LIVE/$rel" || { echo "could not restore $rel" >&2; rc=1; } ;;
    remove) rm -f "$LIVE/$rel" || rc=1 ;;
  esac
done <"$BK/manifest.tsv"
rmdir "$LIVE/no-ai-wall" 2>/dev/null || true
[ "$rc" = 0 ] && echo "undone: ~/.claude is as it was before the install" || echo "undo finished with errors" >&2
exit "$rc"
UNDO
} >"$BK/undo.sh"
chmod 755 "$BK/undo.sh"

# 3. The wall's own folder: the module, the bash helper and the hook, and nothing else.
mkdir -p "$WALL" || fail_after "cannot make $WALL"
for f in "${WALL_FILES[@]}"; do
  cp -p "$SRC/no-ai-wall/$f" "$WALL/.$f.new" && mv -f "$WALL/.$f.new" "$WALL/$f" || fail_after "cannot install no-ai-wall/$f"
done
EXTRA="$(cd "$WALL" && ls -A | grep -v -x -F "$(printf '%s\n' "${WALL_FILES[@]}")" || true)"

# 4. The hook entry in settings.json points at the new folder. Only the path changes; an entry that is already new is left alone, and a
# settings file with no entry for the wall gets one (a wall that is not registered is a wall that is off).
python3 -I - "$LIVE/settings.json" <<'PY' || fail_after "settings.json could not be migrated"
import json, os, sys
p = sys.argv[1]
text = open(p, encoding="utf-8").read()
new = text.replace("hooks/no-ai-wall-guard.py", "no-ai-wall/no-ai-wall-guard.py")
cfg = json.loads(new)
found = [h for g in cfg.get("hooks", {}).get("PreToolUse", []) for h in g.get("hooks", []) if "no-ai-wall-guard.py" in h.get("command", "")]
if not found:
    cfg.setdefault("hooks", {}).setdefault("PreToolUse", []).insert(0, {"hooks": [{"type": "command", "command": "python3 -I ~/.claude/no-ai-wall/no-ai-wall-guard.py", "timeout": 10}]})
    new = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
if new != text:
    mode = os.stat(p).st_mode & 0o777
    tmp = p + ".wall-new"
    open(tmp, "w", encoding="utf-8").write(new)
    os.chmod(tmp, mode)
    os.replace(tmp, p)
PY

# 5. The three old copies go; nothing reads them any more (the hook entry, lane.sh and the module all use the new folder).
for rel in "${LEGACY[@]}"; do rm -f "$LIVE/$rel" || fail_after "cannot remove the old $rel"; done

# 6. The other global files, from the same commit (install_global.py reads git, never the working tree, and refuses a live edit).
TOOLING_OUT=""
if [ -z "$TOOLING_NOTE" ]; then
  TOOLING_OUT="$(python3 -I "$SRC/scripts/install_global.py" --repo "$REPO" --home "$LIVE" --to "$SHA" --include-stale 2>&1)"
  rc=$?
  [ "$rc" -le 1 ] || fail_after "the global install step failed ($(printf '%s' "$TOOLING_OUT" | tail -n 1 | cut -c1-160))"
  TOOLING_NOTE="$(printf '%s' "$TOOLING_OUT" | tail -n 1)"
  REFUSED="$(printf '%s\n' "$TOOLING_OUT" | grep '^REFUSED' || true)"
fi
# A live lane.sh that reads the wall must read it from the new folder; one that still reads the old folder would start no lane at all now
# that step 5 has emptied it. (A lane.sh that does not mention the wall is left alone.)
if [ -f "$LIVE/scripts/lane.sh" ] && grep -q 'no_ai_wall\.sh' "$LIVE/scripts/lane.sh" && ! grep -q 'no-ai-wall/no_ai_wall.sh' "$LIVE/scripts/lane.sh"; then
  fail_after "lane.sh still reads the wall from the folder it just left (run with --with-tooling to bring lane.sh up to date, or update it first)"
fi

# 7. Prove it, through the hook command settings.json registers.
SMOKE_ERR="$STAGE/smoke.err"
SMOKE="$(python3 -I "$SRC/no-ai-wall/smoke.py" "$LIVE" "$HOME" "$STAGE/smoke-refusals.log" 2>"$SMOKE_ERR")"
src=$?
if [ "$src" != 0 ]; then
  reason="the smoke test failed ($(grep -v '^smoke: no no-AI list' "$SMOKE_ERR" | sed 's/^smoke: //' | head -n 2 | tr '\n' ';' | cut -c1-200)) [$SMOKE]"
  fail_after "$reason"
fi

echo "backup:  $BK"
echo "undo:    bash $BK/undo.sh"
[ -z "$EXTRA" ] || echo "note:    $WALL also holds files that are not the wall's: $(printf '%s' "$EXTRA" | tr '\n' ' ')"
[ -n "$TOOLING_NOTE" ] && echo "tooling: $TOOLING_NOTE"
[ -z "${REFUSED:-}" ] || printf '%s\n' "$REFUSED"
grep -q 'no-AI list' "$SMOKE_ERR" && echo "note:    there is no ~/.config/afa/no-ai-paths.txt here, so the wall is off on this machine until one exists"
echo "WALL OK $SHORT $SMOKE"
