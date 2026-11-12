#!/usr/bin/env bash
# Run one command in a closed room: a private copy of the machine's folders where the real home folder cannot be reached at all (ticket 81).
#
# usage: closed_room.sh [--rw DIR]... [--ro DIR]... [--net] -- command [args...]
#
# Why it exists: a test helper once gave each command a throwaway HOME and called that a sandbox. A command that started a shell with an
# emptied environment got the home path from the account list and wrote into the real home. A changed HOME is not containment. Here the
# home path itself, the one the account list names, shows a throwaway folder, so no spelling of the path leads to the real one.
#
# What the room hides, for the command and everything it starts:
#   - the real home folder (and $HOME, when that is another folder): the path shows a throwaway folder holding one empty file, .closed-room
#   - /tmp, /run, /mnt, /media, /var/tmp and /dev/shm: each shows an empty folder
#   - every other process on the machine (a new process list), and, unless --net is given, the network (only a private loopback is left)
#   - the command also cannot unmount any of this: it runs with no capabilities and no way to gain them
# What the room keeps reachable (it is not a jail): everything else the account can read, and anything the account can write outside the home,
# /tmp, /run, /mnt, /media, /var/tmp and shared memory. With --net, the network. Kept folders (the git checkout the command starts in, read-write;
# for a linked worktree also the main checkout's .git folder, read-only; plus every --rw and --ro folder) show their real contents at their
# real paths. This is a room for our own tests, not a jail for hostile code.
#
# How tests use it: conftest.py (through closed_room_entry.py) restarts pytest inside it, and every shell test sources closed_room_guard.sh
# as its first command, which re-runs the test inside it. Inside, CLOSED_ROOM=1, HOME is the throwaway home and TMPDIR is /tmp.
#
# Order of work (each step checked; any failure is one line, "closed room: <what failed>; nothing was run", exit 97, and the command
# never runs outside a proven room): outer part builds the namespaces; inner part, as mapped root, binds the kept folders aside, covers the
# hidden folders, binds the kept folders back, mounts a fresh /proc, re-enters the folder by path, proves the room, and only then starts the
# command as the real uid with no new privileges. The command's exit code is the room's exit code.
set -uo pipefail

die() { echo "closed room: $1; nothing was run" >&2; exit 97; }
usage() { echo "usage: closed_room.sh [--rw DIR]... [--ro DIR]... [--net] -- command [args...]" >&2; exit 2; }
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"

canon() { local d; d="$(cd -- "$1" 2>/dev/null && pwd -P)" || return 1; printf '%s\n' "$d"; }

if [ "${1:-}" = --inner ]; then
  # Inside the new namespaces, as the mapped root, before the command starts. The environment carries the plan (CR_*).
  shift
  [ "${1:-}" = -- ] && shift
  [ $# -ge 1 ] || die "no command"
  T="${CR_T:?}"; RUID="${CR_RUID:?}"; RGID="${CR_RGID:?}"; START="${CR_START:?}"
  mapfile -t HOMES <<<"${CR_HOMES:?}"
  RH="${HOMES[0]}"
  KEEP_MODES=(); KEEP_PATHS=()
  while IFS=$'\t' read -r m p; do [ -n "$p" ] && { KEEP_MODES+=("$m"); KEEP_PATHS+=("$p"); }; done <<<"${CR_KEEP:-}"
  unset CR_T CR_RUID CR_RGID CR_START CR_HOMES CR_KEEP
  # The marker file is opened now, while the throwaway folder can still be reached by path; it is written only when the room is proven.
  exec 8>"$T/started" || die "cannot open the start marker"

  # 1. Nothing mounted below may leak back out of this namespace.
  mount --make-rprivate / || die "cannot make the mounts private"

  # 2. Hold every kept folder inside the throwaway home (reachable by path while T still is), before anything is covered. The home cover below
  #    is recursive, so the held binds come along with it, and /tmp (which may hold T itself) can then be covered safely.
  mkdir -p "$T/home/.cr-hold" || die "cannot make the holding folder"
  for i in "${!KEEP_PATHS[@]}"; do
    mkdir -p "$T/home/.cr-hold/$i" && mount --bind "${KEEP_PATHS[$i]}" "$T/home/.cr-hold/$i" || die "cannot hold ${KEEP_PATHS[$i]}"
  done

  # 3. Cover. Extra home spellings first (plain bind, no held folders), the account-list home last (recursive bind, held folders included).
  for ((i = ${#HOMES[@]} - 1; i >= 1; i--)); do
    mount --bind "$T/home" "${HOMES[$i]}" || die "cannot cover ${HOMES[$i]}"
  done
  mount --rbind "$T/home" "$RH" || die "cannot cover the home folder"
  # The copies under the home path stay; the originals under T go, or the holding folders could not be removed later (still mount points).
  for i in "${!KEEP_PATHS[@]}"; do umount "$T/home/.cr-hold/$i" || die "cannot release the original holding place"; done
  mount --bind "$T/tmp" /tmp || die "cannot cover /tmp"
  for d in /run /mnt /dev/shm /var/tmp /media; do
    [ -d "$d" ] || continue
    mount -t tmpfs -o mode=0755,size=1m tmpfs "$d" || die "cannot cover $d"
  done

  # 4. Re-create each kept folder's path and bind it back; read-only ones are remounted read-only.
  for i in "${!KEEP_PATHS[@]}"; do
    p="${KEEP_PATHS[$i]}"
    mkdir -p "$p" || die "cannot re-create $p"
    mount --bind "$RH/.cr-hold/$i" "$p" || die "cannot bind $p back"
    if [ "${KEEP_MODES[$i]}" = ro ]; then mount -o remount,bind,ro "$p" || die "cannot make $p read-only"; fi
  done
  for i in "${!KEEP_PATHS[@]}"; do umount "$RH/.cr-hold/$i" || die "cannot release the holding place"; done
  rmdir "$RH"/.cr-hold/* "$RH/.cr-hold" 2>/dev/null
  [ ! -e "$RH/.cr-hold" ] || die "the holding folder is still in the throwaway home"

  # 5. A fresh /proc for the new process list; a private loopback unless the network was kept.
  mount -t proc proc /proc || die "cannot mount a fresh /proc"
  # mount(8) keeps its own bookkeeping under /run/mount; it is ours (the tmpfs above), and /run must look empty to the command.
  [ -d /run/mount ] && rm -rf /run/mount
  if [ "${CR_NET:-0}" != 1 ]; then
    ip link set lo up 2>/dev/null || python3 -c '
import fcntl, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
f = struct.unpack("16sH14x", fcntl.ioctl(s, 0x8913, struct.pack("16sH14x", b"lo", 0)))[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", f | 1))' 2>/dev/null || echo "closed room: warning: the loopback could not be brought up" >&2
  fi
  unset CR_NET

  # 6. Go back into the starting folder BY PATH. A folder handle held from before the mounts (the shell's own working folder) still points into
  #    the real home; re-entering by path is what closes that route. A start outside every kept folder goes to the throwaway home.
  inside=0
  for p in "${KEEP_PATHS[@]}"; do
    case "$START/" in "$p"/*) inside=1 ;; esac
  done
  if [ "$inside" = 1 ]; then cd -- "$START" || die "cannot enter $START"; else cd -- "$RH" || die "cannot enter the throwaway home"; fi

  # 7. Prove the room. Fail closed.
  [ "$(getent passwd "$RUID" | cut -d: -f6)" = "$RH" ] || die "the account-list home changed"
  [ -e "$RH/.closed-room" ] || die "the sentinel is not at the home path"
  allowed=" .closed-room "
  for p in "${KEEP_PATHS[@]}"; do
    case "$p/" in "$RH"/*) rest="${p#"$RH"/}"; allowed="$allowed${rest%%/*} " ;; esac
  done
  while IFS= read -r name; do
    case "$allowed" in *" $name "*) ;; *) die "the home path shows something besides the sentinel: $name" ;; esac
  done < <(ls -A -- "$RH")
  [ "$$" = 1 ] || die "this is not the first process of a new process list"
  # A builtin read, so /proc/self is this shell: the new /proc calls it 1, the old one would show its number outside the room.
  selfpid=""
  read -r selfpid _ < /proc/self/stat 2>/dev/null || selfpid=""
  [ "$selfpid" = 1 ] || die "/proc does not show the new process list"
  for i in "${!KEEP_PATHS[@]}"; do
    if [ "${KEEP_MODES[$i]}" = ro ] && test -w "${KEEP_PATHS[$i]}"; then die "${KEEP_PATHS[$i]} is not read-only"; fi
  done
  # The step that follows the last check must work, or the command would not start after the marker is written.
  unshare -U --map-user="$RUID" --map-group="$RGID" -- setpriv --no-new-privs env --default-signal=INT,QUIT true || die "cannot drop to the real user, or this env has no --default-signal option"
  echo started >&8
  exec 8>&-

  # 8. The real uid, no capabilities left over, no way to gain any. The command runs as a child, not in place of this process: the first process
  #    of a pid namespace gets no default signal handling (a plain kill does nothing to it) and nothing above it in a process-ancestry walk counts
  #    pid 1, so a test that walks its parents would lose the chain. This process stays as the room's init, passes the signals on, and returns
  #    the command's exit code; when it ends, the kernel ends everything left in the room. A script's background command starts with INT and
  #    QUIT ignored, and the command would inherit that (Ctrl-C would do nothing), so env puts both back to their defaults.
  unshare -U --map-user="$RUID" --map-group="$RGID" -- setpriv --no-new-privs env --default-signal=INT,QUIT CLOSED_ROOM=1 HOME="$RH" TMPDIR=/tmp "$@" <&0 &
  child=$!
  trap 'kill -TERM "$child" 2>/dev/null' TERM
  trap 'kill -INT "$child" 2>/dev/null' INT
  trap 'kill -HUP "$child" 2>/dev/null' HUP
  wait "$child"; rc=$?
  # A trapped signal ends the first wait early; wait again for the real exit code.
  while kill -0 "$child" 2>/dev/null; do wait "$child"; rc=$?; done
  exit "$rc"
fi

# ---- outer part ----
rw=(); ro=(); net=0
while [ $# -gt 0 ]; do
  case "$1" in
    --rw) [ $# -ge 2 ] || usage; rw+=("$2"); shift 2 ;;
    --ro) [ $# -ge 2 ] || usage; ro+=("$2"); shift 2 ;;
    --net) net=1; shift ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ $# -ge 1 ] || usage

for tool in unshare setpriv mount getent python3; do command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed"; done
unshare --help 2>&1 | grep -q -- '--map-user' || die "this unshare ($(unshare --version 2>&1 | head -n 1)) has no --map-user"

RUID="$(id -u)"; RGID="$(id -g)"
RH="$(getent passwd "$RUID" | cut -d: -f6)"
[ -n "$RH" ] && [ -d "$RH" ] || die "the account list gives no home folder"
RH="$(canon "$RH")" || die "cannot resolve the home folder"
HOMES="$RH"
if [ -n "${HOME:-}" ] && [ -d "$HOME" ]; then
  H2="$(canon "$HOME")" || die "cannot resolve \$HOME"
  [ "$H2" = "$RH" ] || HOMES="$RH"$'\n'"$H2"
fi
START="$(pwd -P)" || die "cannot read the current folder"

# Kept folders.
KEEP=""
add_keep() {
  local mode="$1" p
  p="$(canon "$2")" || die "kept folder is not a folder: $2"
  case "$p" in *$'\n'* | *$'\t'*) die "kept folder name has a tab or newline" ;; esac
  # The real home itself, / and everything above the home would show the real home again.
  if [ "$p" = / ] || [ "$p" = "$RH" ] || [ "${RH#"$p"/}" != "$RH" ]; then die "refusing to keep $p (it is, or is above, the real home folder)"; fi
  case "$KEEP" in *$'\t'"$p"$'\n'*) return 0 ;; esac
  KEEP="$KEEP$mode"$'\t'"$p"$'\n'
}
top="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$top" ] || top="$START"
add_keep rw "$top"
gd="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)"
gc="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
if [ -n "$gd" ] && [ -n "$gc" ] && [ "$(canon "$gd")" != "$(canon "$gc")" ]; then add_keep ro "$gc"; fi
for d in ${rw[@]+"${rw[@]}"}; do add_keep rw "$d"; done
for d in ${ro[@]+"${ro[@]}"}; do add_keep ro "$d"; done
# Parents bind before the folders inside them.
KEEP="$(printf '%s' "$KEEP" | awk -F'\t' '{ n = gsub("/", "/", $2); printf "%04d\t%s\t%s\n", n, $1, $2 }' | sort | awk -F'\t' '{ print $2 "\t" $3 }')"$'\n'

T="$(mktemp -d "${TMPDIR:-/tmp}/closed-room.XXXXXX")" || die "cannot make the throwaway folder"
trap 'rm -rf "$T"' EXIT
mkdir "$T/home" "$T/tmp" && : >"$T/home/.closed-room" || die "cannot fill the throwaway folder"

flags=(-U -r -m -p -f -i --kill-child)
[ "$net" = 1 ] || flags+=(-n)
CR_T="$T" CR_RUID="$RUID" CR_RGID="$RGID" CR_START="$START" CR_HOMES="$HOMES" CR_KEEP="$KEEP" CR_NET="$net" \
  unshare "${flags[@]}" "$SELF" --inner -- "$@"
rc=$?
# Only the inner part writes the marker, and only after the room has been proven. Without it the command did not run, whatever the code is
# (a failing stand-in for unshare gives 1, which a command could also give).
if [ ! -s "$T/started" ]; then
  [ "$rc" = 97 ] || echo "closed room: could not build the room (exit $rc); nothing was run" >&2
  exit 97
fi
exit "$rc"
