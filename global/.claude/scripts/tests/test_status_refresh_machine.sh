#!/usr/bin/env bash
# Tests for the machine switch in openclaw_status_refresh.sh and the per-machine runner name in he_runner_watch.sh (fleet review finding 3,
# 28 Oct 2026): NX-01 runs every step; any other machine runs only the keeper steps. Stub scripts record what ran; nothing real is touched.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/status-machine.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }

setup() {  # <machine> -> $H (home), $S (scripts dir), $CALLS
  H="$TMP/home-$1"; S="$H/.claude/scripts"; CALLS="$H/calls"
  mkdir -p "$S" "$H/.claude/state" "$H/.config/afa" "$H/bin"
  echo "$1" >"$H/.config/afa/machine"
  cp "$SRC/openclaw_status_refresh.sh" "$S/"
  : >"$CALLS"
  local f
  for f in wsl_bridge_watchdog.py hub_inbox_archive.py hub_digest.py notes_check.py hours.py openclaw_dispatch.py openclaw_reconcile.py openclaw_status_digest.py; do
    printf 'import sys\nopen(%s, "a").write("%s\\n")\nsys.exit(0)\n' "'$CALLS'" "$f" >"$S/$f"
  done
  for f in lane_watch.sh he_runner_watch.sh fleet-up.sh; do
    printf '#!/usr/bin/env bash\necho %s >>"%s"\n' "$f" "$CALLS" >"$S/$f"; chmod +x "$S/$f"
  done
  printf '#!/usr/bin/env bash\necho wsl.exe >>"%s"\ncat >/dev/null\n' "$CALLS" >"$H/bin/wsl.exe"; chmod +x "$H/bin/wsl.exe"
}
run_refresh() { HOME="$H" PATH="$H/bin:$PATH" bash "$S/openclaw_status_refresh.sh" >/dev/null 2>&1 || true; }

# 1. Another machine runs only the keeper steps: bridge probe, recovery check (fleet-up.sh), lane recycle, inbox archive, hub digest.
setup NCC-TEST
run_refresh
got="$(sort "$CALLS" | tr '\n' ' ')"
[ "$got" = "fleet-up.sh hub_digest.py hub_inbox_archive.py lane_watch.sh wsl_bridge_watchdog.py " ] || fail "a keeper machine runs only the keeper steps (ran: $got)"; ok

# 2. NX-01 runs the rest too.
setup NX-01
run_refresh
for want in fleet-up.sh he_runner_watch.sh notes_check.py openclaw_dispatch.py openclaw_reconcile.py hub_digest.py lane_watch.sh; do
  grep -qx "$want" "$CALLS" || fail "NX-01 runs $want (ran: $(tr '\n' ' ' <"$CALLS"))"
done; ok

# 3. A missing machine file counts as another machine (doing less is the safe mistake).
setup NCC-TEST
rm "$H/.config/afa/machine"
run_refresh
! grep -qx he_runner_watch.sh "$CALLS" || fail "no machine file: the runner watch does not run"; ok

# 4. The runner name is per machine: the tower asks GitHub about its own name, never NX-01's, and starts nothing without its own folder.
setup NCC-TEST
cp "$SRC/he_runner_watch.sh" "$S/"
ARGS="$H/gh-args"
cat >"$H/bin/gh" <<GH
#!/usr/bin/env bash
echo "\$*" >>"$ARGS"
case "\$*" in *RUNNER_LABEL*) echo workstation ;; esac
GH
chmod +x "$H/bin/gh"
HOME="$H" PATH="$H/bin:$PATH" bash "$S/he_runner_watch.sh" --check >/dev/null 2>&1 || true
grep -q 'ncc-test-wsl2' "$ARGS" || fail "the tower looks up its own runner name: $(cat "$ARGS")"; ok
! grep -q 'workstation-wsl2' "$ARGS" || fail "the tower never names NX-01's runner"; ok
HOME="$H" PATH="$H/bin:$PATH" bash "$S/he_runner_watch.sh" >/dev/null 2>&1 || fail "no runner folder on the tower is not an error"; ok
! grep -q 'registration-token' "$ARGS" || fail "the tower never asks for a registration token without a runner folder"; ok

printf 'ok: %s checks\n' "$checks"
