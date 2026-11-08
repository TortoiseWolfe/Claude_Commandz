#!/usr/bin/env bash
# Run one command with every settings file hidden from it and from everything it starts (issue 332).
#
# usage: lane_hide_secrets.sh <checkout> <hand-off folder> <prompt file> -- <command> [args...]
#
# lane.sh puts this in front of every engine that is not Claude (codex, copilot, agy, opencode, local), so the rule lives in one place and does not
# depend on an engine's own deny list. What it does, in order:
#   1. Scans the prompt file and the hand-off folder against the real secret values on this machine (known_secrets_scan.py, strict). A match refuses
#      the start (exit 98); only names are printed, never values.
#   2. Lists the settings files (lane_secret_paths.py: env files, key files, sealed-key folders, ~/.config/afa/secrets, ~/.config/afa/rotated-test-users)
#      under the checkout, the extra folders in LANE_HIDE_EXTRA_ROOTS (colon-separated) and ~/repos (LANE_HIDE_REPOS overrides it).
#   3. Reads the values the lane was granted by name (LANE_SECRET_ENV=NAME[,NAME], from ~/.config/afa/secrets/NAME) into its environment, before step 4.
#   4. Enters a new user and mount namespace (unshare -Urm), binds /dev/null over each settings file and an empty tmpfs over each settings folder,
#      checks each one now reads empty, then drops every capability (setpriv) so nothing the engine runs can unmount them or mount over them, and runs
#      the command. Any failure before the command starts exits 97 and the command never runs.
#   5. After the command ends, rewrites the lane's out.log with scrub_known_secrets.py (best effort), and does the same to every file in OpenCode's
#      tool-output folder (~/.local/share/opencode/tool-output, LANE_TOOL_OUTPUT_DIR overrides it) that was written since step 4 began: OpenCode saves
#      the full output of each tool call there, so a password a command printed stays readable to the next lane until it is scrubbed. Older files
#      there are not touched, and a lane can still read a file from its own run while it runs.
#   6. Scrubs OpenCode's database (~/.local/share/opencode/opencode.db, LANE_OPENCODE_DB overrides it) with SQL: scrub_sqlite_secrets.py takes a
#      backup, replaces the values in a transaction, checks integrity, vacuums, and rescans the file for 0 values. It refuses (exit 3) while any
#      other process holds the database, and never ends one; a later lane's end scrubs it.
#
# Limits, stated plainly: a settings file created after the lane starts is not hidden until the next start; a secret the lane is granted by name sits in
# the environment of that lane, so it can print it; the engine's own provider key files (~/.config/gemini, ~/.config/openrouter) stay readable because
# the engine needs them to run. Needs unprivileged user namespaces (Linux; on Ubuntu 24.04 runners: sysctl kernel.apparmor_restrict_unprivileged_userns=0).
set -uo pipefail

die() { echo "lane_hide_secrets: $*" >&2; exit "${2:-97}"; }
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SELF")"

if [ "${1:-}" = --inner ]; then
  # Inside the namespace, as the mapped root, before the command starts.
  shift
  listfile="$1"; shift
  [ "${1:-}" = -- ] && shift
  [ $# -ge 1 ] || die "no command"
  paths=()
  while IFS= read -r -d '' entry; do paths+=("$entry"); done <"$listfile"
  for entry in ${paths[@]+"${paths[@]}"}; do
    kind="${entry:0:1}"; path="${entry:1}"
    if [ "$kind" = D ]; then
      mount -t tmpfs -o mode=0555,size=4k tmpfs "$path" 2>/dev/null || die "cannot hide folder $path"
      [ -z "$(ls -A -- "$path" 2>/dev/null)" ] || die "folder still shows files after hiding: $path"
    else
      mount --bind /dev/null "$path" 2>/dev/null || die "cannot hide file $path"
      [ ! -s "$path" ] || die "file still has content after hiding: $path"
    fi
  done
  # Inside the namespace the user is root and system files look foreign, which makes ssh refuse /etc/ssh/ssh_config.d; point it at the user's own config.
  : "${GIT_SSH_COMMAND:=ssh -F $HOME/.ssh/config}"; export GIT_SSH_COMMAND
  # No capabilities and no new ones: the command cannot umount what was hidden above or mount over it.
  exec setpriv --bounding-set=-all --inh-caps=-all --no-new-privs "$@"
fi

[ $# -ge 4 ] || die "usage: lane_hide_secrets.sh <checkout> <hand-off folder> <prompt file> -- <command> [args...]"
checkout="$1"; hand="$2"; prompt="$3"; shift 3
[ "${1:-}" = -- ] || die "usage: lane_hide_secrets.sh <checkout> <hand-off folder> <prompt file> -- <command> [args...]"
shift
[ -d "$checkout" ] || die "not a folder: $checkout"
for tool in unshare setpriv mount python3; do command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed; the lane is not started"; done

# 1. Scan what is about to reach the provider.
scan_err="$(mktemp)"; scan_out="$(mktemp)"; listfile="$(mktemp)"
trap 'rm -f "$scan_err" "$scan_out" "$listfile"' EXIT
scan_targets=()
[ -e "$prompt" ] && scan_targets+=("$prompt")
[ -d "$hand" ] && scan_targets+=("$hand")
if [ ${#scan_targets[@]} -gt 0 ]; then
  python3 -I "$SCRIPT_DIR/known_secrets_scan.py" --strict --repos "${LANE_SCAN_REPOS:-$HOME/repos}" "${scan_targets[@]}" >"$scan_out" 2>"$scan_err"
  rc=$?
  if [ "$rc" = 1 ]; then
    cat "$scan_out" >&2
    die "a real secret value is in the prompt or the hand-off folder (names above); the lane is not started" 98
  elif [ "$rc" != 0 ]; then
    # Exit 2 with this message means this machine has no env file values at all, so there is nothing that could leak.
    if [ "$rc" = 2 ] && grep -q 'nothing to check against' "$scan_err"; then :; else
      cat "$scan_err" >&2
      die "the secret scan could not run (exit $rc); the lane is not started" 98
    fi
  fi
fi

# 2. What to hide.
roots=("$checkout")
IFS=: read -r -a extra <<<"${LANE_HIDE_EXTRA_ROOTS:-}"
for r in ${extra[@]+"${extra[@]}"}; do [ -n "$r" ] && roots+=("$r"); done
[ -d "${LANE_HIDE_REPOS:-$HOME/repos}" ] && roots+=("${LANE_HIDE_REPOS:-$HOME/repos}")
python3 -I "$SCRIPT_DIR/lane_secret_paths.py" hide "${roots[@]}" >"$listfile" || die "could not list the settings files; the lane is not started"

# 3. Secrets the lane is granted by name, read before anything is hidden.
if [ -n "${LANE_SECRET_ENV:-}" ]; then
  IFS=, read -r -a names <<<"$LANE_SECRET_ENV"
  for n in "${names[@]}"; do
    [[ "$n" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "LANE_SECRET_ENV name is not a plain variable name: $n"
    f="${KNOWN_SECRETS_DIR:-$HOME/.config/afa/secrets}/$n"
    [ -f "$f" ] || die "no secret file for $n; the lane is not started"
    export "$n=$(<"$f")"
  done
fi

# 4. Run the command with the settings files hidden.
started="$(mktemp)"; touch -d '1 second ago' "$started"
trap 'rm -f "$scan_err" "$scan_out" "$listfile" "$started"' EXIT
unshare -Urm "$SELF" --inner "$listfile" -- "$@"
rc=$?
# A failure to set up shows as exit 97 or 98 from the inner step; the command's own exit code passes through.

# 5. Scrub the run's log.
if [ -f "$hand/out.log" ]; then
  python3 -I "$SCRIPT_DIR/scrub_known_secrets.py" --min-age 0 --repos "${LANE_SCAN_REPOS:-$HOME/repos}" "$hand/out.log" >/dev/null 2>&1 || true
fi
# Tool-output files the run wrote (OpenCode keeps each tool call's full output there).
tool_out="${LANE_TOOL_OUTPUT_DIR:-$HOME/.local/share/opencode/tool-output}"
if [ -d "$tool_out" ]; then
  find "$tool_out" -type f -newer "$started" -print0 2>/dev/null |
    xargs -0 -r python3 -I "$SCRIPT_DIR/scrub_known_secrets.py" --min-age 0 --repos "${LANE_SCAN_REPOS:-$HOME/repos}" >&2 ||
    echo "lane_hide_secrets: WARNING the tool-output scrub failed; check $tool_out for secret values" >&2
fi

# 6. OpenCode's database (~/.local/share/opencode/opencode.db, LANE_OPENCODE_DB overrides it) keeps every message and tool result of every session.
# scrub_sqlite_secrets.py replaces the values with SQL, inside a transaction, after a backup, and only when no other process holds the database
# (exit 3: the last lane to end scrubs it). It never ends a process.
oc_db="${LANE_OPENCODE_DB:-$HOME/.local/share/opencode/opencode.db}"
if [ -f "$oc_db" ]; then
  python3 -I "$SCRIPT_DIR/scrub_sqlite_secrets.py" --repos "${LANE_SCAN_REPOS:-$HOME/repos}" "$oc_db" >&2
  db_rc=$?
  case "$db_rc" in
    0) ;;
    3) echo "lane_hide_secrets: another OpenCode process holds $oc_db; the last lane to end scrubs it" >&2 ;;
    *) echo "lane_hide_secrets: WARNING the database scrub failed (exit $db_rc); check $oc_db for secret values" >&2 ;;
  esac
fi
exit "$rc"
