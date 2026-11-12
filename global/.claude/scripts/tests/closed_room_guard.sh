# Sourced by a shell test as its first command after `set` (ticket 81):
#
#   . "$(dirname "${BASH_SOURCE[0]}")/closed_room_guard.sh"
#
# Not yet in a proven closed room: the test starts again from the top inside closed_room.sh (exec), so the real home folder cannot be reached
# by anything the test runs, whatever it does with HOME. Already in one: this returns and the test goes on. A room counts as proven only when
# CLOSED_ROOM=1 AND the account-list home holds .closed-room; the variable alone is not trusted. If the room cannot be built, closed_room.sh
# says so and exits 97; there is no unprotected run on Linux. Not Linux (no namespaces): this returns with a line on stderr.
# test_closed_room.sh is the one test that does not source this: it tests the room from outside.
__cr_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [ "$(uname -s)" != Linux ]; then
  echo "closed room: not available on this system; this test runs without it" >&2
else
  __cr_home="$(getent passwd "$(id -u)" | cut -d: -f6)"
  if [ "${CLOSED_ROOM:-}" = 1 ] && [ -n "$__cr_home" ] && [ -e "$__cr_home/.closed-room" ]; then
    :
  else
    # Start in the checkout that holds this test when the current folder is in another one (or none), so the test's own files are kept.
    __cr_self="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
    if [ "$(git rev-parse --show-toplevel 2>/dev/null)" != "$(git -C "$__cr_dir" rev-parse --show-toplevel 2>/dev/null)" ]; then cd "$__cr_dir" || exit 97; fi
    # Tools installed under the home (a node from a version manager) are found through PATH; their folders stay visible, read-only.
    __cr_ro=()
    while IFS= read -r -d '' __cr_p; do
      __cr_p="$(cd -- "$__cr_p" 2>/dev/null && pwd -P)" || continue
      case "$__cr_p/" in
        "$__cr_home/.claude/"* | "$__cr_home/.config/"* | "$__cr_home/.ssh/"* | "$__cr_home/.gnupg/"*) continue ;;
        "$__cr_home/"?*) __cr_ro+=(--ro "$__cr_p") ;;
      esac
    done < <(printf '%s' "$PATH" | tr ':' '\0')
    exec "$__cr_dir/../closed_room.sh" ${__cr_ro[@]+"${__cr_ro[@]}"} -- bash "$__cr_self" "$@"
  fi
  unset __cr_home
fi
unset __cr_dir
