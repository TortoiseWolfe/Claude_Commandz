#!/usr/bin/env bash
# Tests for lane.sh refusing an engine whose program this machine does not have (28 Oct 2026: the tower has no agy or OpenCode, and the
# dispatcher's agy-first order started lanes whose windows died at once on "command not found"). A fake HOME and PATH hold everything.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd -P)/lane.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/lane-enginecheck.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; HOME_T="$TMP/home"; WORK="$HOME_T/repos/proj"
mkdir -p "$BIN" "$WORK" "$HOME_T/.claude/state/lanes" "$HOME_T/.config/afa"
echo NCC-TEST >"$HOME_T/.config/afa/machine"
for b in tmux sleep claude; do printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/$b"; chmod +x "$BIN/$b"; done
printf 'A mission.\n' >"$TMP/p.md"

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
lane2() { local extra="$1"; shift; set +e; LAST_OUT="$(HOME="$HOME_T" PATH="$BIN:/usr/bin:/bin" AFA_ENGINES_AVAILABLE="$extra" bash "$SCRIPT" "$@" 2>&1)"; LAST_RC=$?; set -e; }

# 1. A missing engine is refused with the engine and the machine named, before anything is created.
lane2 "" --engine agy lane1 "$WORK" sonnet "$TMP/p.md"
[ "$LAST_RC" = 1 ] || fail "a missing engine is refused ($LAST_RC: $LAST_OUT)"; ok
case "$LAST_OUT" in *"refusing engine agy"*"not installed on this machine (NCC-TEST)"*) ok ;; *) fail "the message names the engine and machine: $LAST_OUT" ;; esac
[ ! -e "$HOME_T/.claude/state/lanes/index.tsv" ] || fail "a refused start writes no index row"; ok

# 2. It is refused under --dry-run too, so a dry run shows what a real start would do.
lane2 "" --dry-run --engine opencode lane1 "$WORK" sonnet "$TMP/p.md"
[ "$LAST_RC" = 1 ] || fail "a dry run refuses a missing engine ($LAST_OUT)"; ok

# 3. An engine found on PATH passes the check (here a fake codex).
printf '#!/usr/bin/env bash\nexit 0\n' >"$BIN/codex"; chmod +x "$BIN/codex"
lane2 "" --dry-run --engine codex lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "an installed engine is not refused: $LAST_OUT" ;; *) ok ;; esac

# 4. Claude, which every machine here has, is not refused.
lane2 "" --dry-run lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "claude is found on PATH: $LAST_OUT" ;; *) ok ;; esac

# 5. AFA_ENGINES_AVAILABLE names an engine as present (a comma list, or *) when the shell's PATH cannot show it.
lane2 "agy,copilot" --dry-run --engine agy lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "a listed engine passes: $LAST_OUT" ;; *) ok ;; esac
lane2 "copilot" --dry-run --engine agy lane1 "$WORK" sonnet "$TMP/p.md"
[ "$LAST_RC" = 1 ] || fail "an engine missing from the list is still refused"; ok
lane2 "*" --dry-run --engine opencode lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "* lets every engine through: $LAST_OUT" ;; *) ok ;; esac

# 6. An engine only in ~/.local/bin (a scheduled task's PATH lacks it) is found, so NX-01's code lanes do not fall to Claude.
mkdir -p "$HOME_T/.local/bin"; printf '#!/usr/bin/env bash\nexit 0\n' >"$HOME_T/.local/bin/agy"
lane2 "" --dry-run --engine agy lane1 "$WORK" sonnet "$TMP/p.md"
[ "$LAST_RC" = 1 ] || fail "a file that is not executable is not an engine"; ok
chmod +x "$HOME_T/.local/bin/agy"
lane2 "" --dry-run --engine agy lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "an engine only in ~/.local/bin is found: $LAST_OUT" ;; *) ok ;; esac
rm -f "$HOME_T/.local/bin/agy"

# 7. An engine only on the PATH of the user's login and interactive shell is found.
mkdir -p "$HOME_T/extra"; printf '#!/usr/bin/env bash\nexit 0\n' >"$HOME_T/extra/opencode"; chmod +x "$HOME_T/extra/opencode"
lane2 "" --dry-run --engine opencode lane1 "$WORK" sonnet "$TMP/p.md"
[ "$LAST_RC" = 1 ] || fail "opencode is missing before the login profile names its folder"; ok
printf 'export PATH="%s/extra:$PATH"\n' "$HOME_T" >"$HOME_T/.profile"
lane2 "" --dry-run --engine opencode lane1 "$WORK" sonnet "$TMP/p.md"
case "$LAST_OUT" in *"not installed on this machine"*) fail "an engine on the login shell's PATH is found: $LAST_OUT" ;; *) ok ;; esac

printf 'ok: %s checks\n' "$checks"
