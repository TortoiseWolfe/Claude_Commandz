#!/usr/bin/env bash
# openclaw_ask_install.sh: put the hub-ask hook inside OpenClaw (a managed hook in its state dir).
#
#   openclaw_ask_install.sh apply      copy openclaw_ask_hook/{HOOK.md,handler.js} to ~/.openclaw/hooks/hub-ask, list hooks
#   openclaw_ask_install.sh verify     show whether the gateway lists hub-ask as ready (changes nothing)
#   openclaw_ask_install.sh rollback   disable hub-ask and move it aside (nothing is deleted)
#
# Runs from the hub; the only thing it does inside the OpenClawGateway distro is write that one folder as the
# openclaw user. No model, channel, key or config value is touched. The hook is trusted gateway code, not a
# tool: read handler.js before applying.
set -u
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
src="$here/openclaw_ask_hook"
oc() { timeout 90 wsl.exe -d OpenClawGateway -u openclaw -- "$@"; }

case "${1:-}" in
  apply)
    [ -f "$src/HOOK.md" ] && [ -f "$src/handler.js" ] || { echo "hook files missing in $src"; exit 1; }
    tar -C "$src" -c HOOK.md handler.js | oc bash -c 'set -e; d=/home/openclaw/.openclaw/hooks/hub-ask; mkdir -p "$d"; tar -x -C "$d" -f -; ls -l "$d"' || exit 1
    oc bash -c 'cd /home/openclaw; openclaw hooks list 2>&1 | grep -E "hub-ask|Hooks \("'
    ;;
  verify)
    oc bash -c 'cd /home/openclaw; openclaw hooks list 2>&1 | grep -E "hub-ask|Hooks \("; ls /home/openclaw/.openclaw/hooks 2>&1'
    ;;
  rollback)
    oc bash -c 'cd /home/openclaw; openclaw hooks disable hub-ask 2>&1 | tail -1; d=/home/openclaw/.openclaw/hooks; [ -d "$d/hub-ask" ] && mv "$d/hub-ask" "$d/../hub-ask.removed-$(date +%Y%m%d%H%M%S)"; ls "$d"'
    ;;
  *) echo "usage: openclaw_ask_install.sh apply|verify|rollback" >&2; exit 2 ;;
esac
