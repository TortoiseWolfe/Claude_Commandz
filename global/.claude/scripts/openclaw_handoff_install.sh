#!/usr/bin/env bash
# openclaw_handoff_install.sh: put the session-handoff hook inside OpenClaw (a managed hook in its state dir).
#
#   openclaw_handoff_install.sh apply      copy openclaw_handoff_hook/{HOOK.md,handler.js,handoff.js} to ~/.openclaw/hooks/session-handoff, list hooks
#   openclaw_handoff_install.sh verify     show whether the gateway lists session-handoff as ready (changes nothing)
#   openclaw_handoff_install.sh rollback   disable session-handoff and move it aside (nothing is deleted)
#
# Runs from the hub; the only thing it does inside the OpenClawGateway distro is write that one folder as the
# openclaw user. No model, channel, key or config value is touched. The hook is trusted gateway code, not a
# tool: read handler.js before applying.
set -u
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
src="$here/openclaw_handoff_hook"
# -e, not --: after "--" wsl.exe re-parses the command line through a shell, which empties "$d" and friends.
oc() { timeout 90 wsl.exe -d OpenClawGateway -u openclaw -e "$@"; }

case "${1:-}" in
  apply)
    [ -f "$src/HOOK.md" ] && [ -f "$src/handler.js" ] && [ -f "$src/handoff.js" ] || { echo "hook files missing in $src"; exit 1; }
    tar -C "$src" -c HOOK.md handler.js handoff.js | oc bash -c 'set -e; d=/home/openclaw/.openclaw/hooks/session-handoff; mkdir -p "$d"; tar -x -C "$d" -f -; ls -l "$d"' || exit 1
    oc bash -c 'cd /home/openclaw; openclaw hooks list 2>&1 | grep -E "session-handoff|Hooks \("'
    ;;
  verify)
    oc bash -c 'cd /home/openclaw; openclaw hooks list 2>&1 | grep -E "session-handoff|Hooks \("; ls /home/openclaw/.openclaw/hooks 2>&1'
    ;;
  rollback)
    oc bash -c 'cd /home/openclaw; openclaw hooks disable session-handoff 2>&1 | tail -1; d=/home/openclaw/.openclaw/hooks; [ -d "$d/session-handoff" ] && mv "$d/session-handoff" "$d/../session-handoff.removed-$(date +%Y%m%d%H%M%S)"; ls "$d"'
    ;;
  *) echo "usage: openclaw_handoff_install.sh apply|verify|rollback" >&2; exit 2 ;;
esac
