#!/usr/bin/env bash
# Tests for closed_room.sh (ticket 81): a command run in the room cannot reach the real home folder, /tmp, /run or /mnt, not even by asking the
# account list for the home path or by starting a shell with an emptied environment (the 12 Nov case).
# Fixed, hand-written commands only. Every write probe is guarded, `test -e ~/.closed-room && : > ~/probe`, so it cannot run when the room failed.
# This file does NOT source closed_room_guard.sh: it tests the room from outside, so the real home it looks at is the real one.
# There is no skip path: a machine that cannot make the namespaces fails here with a line saying so.
set -euo pipefail
unset CLOSED_ROOM

ROOM="$(cd "$(dirname "$0")/.." && pwd -P)/closed_room.sh"
HERE="$(cd "$(dirname "$0")/.." && pwd -P)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/closed-room-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

checks=0
fail() { printf 'not ok: %s\n' "$1" >&2; exit 1; }
ok() { checks=$((checks + 1)); }
assert_eq() { [ "$1" = "$2" ] && ok || fail "$3 (got: $1, wanted: $2)"; }
assert_contains() { case "$1" in *"$2"*) ok ;; *) fail "$3 (missing: $2 in: $1)" ;; esac; }
assert_not_contains() { case "$1" in *"$2"*) fail "$3 (unexpected: $2 in: $1)" ;; *) ok ;; esac; }

unshare -Urm true 2>/dev/null || fail 'this machine cannot make a user and mount namespace (unshare -Urm); on Ubuntu 24.04 run: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0'
command -v setpriv >/dev/null || fail 'setpriv is not installed'

REAL_HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
UID_OUT="$(id -u)"

# A scratch checkout under the test's own temporary folder, so nothing is written into the worktree this file lives in.
CK="$TMP/checkout"
mkdir "$CK"
git -C "$CK" init -q
git -C "$CK" -c user.name=t -c user.email=t@example.com commit -q --allow-empty -m first
WT="$TMP/linked-worktree"
git -C "$CK" worktree add -q "$WT"

# Run a command in the room, starting in the scratch checkout. Output and exit code in ROUT and RRC.
room() { RRC=0; ROUT="$(cd "$CK" && timeout 120 "$ROOM" "$@" 2>&1)" || RRC=$?; }
# The same, from a given folder.
room_in() { local d="$1"; shift; RRC=0; ROUT="$(cd "$d" && timeout 120 "$ROOM" "$@" 2>&1)" || RRC=$?; }

# 1. The room works at all, and the command runs as the same user.
room -- id -u
assert_eq "$RRC" 0 "a plain command runs in the room"
assert_eq "$ROUT" "$UID_OUT" "the user id inside is the outer user id"

# 2. HOME, ~ and the account-list path are one path, and it holds the sentinel.
room -- bash -c 'h="$(getent passwd "$(id -u)" | cut -d: -f6)"; [ "$HOME" = "$h" ] && [ ~ = "$h" ] && test -e "$h/.closed-room" && echo same-path'
assert_eq "$ROUT" same-path "HOME, ~ and the account-list home are one path holding .closed-room"
room -- bash -c 'echo "$CLOSED_ROOM $TMPDIR"'
assert_eq "$ROUT" "1 /tmp" "CLOSED_ROOM and TMPDIR are set inside"

# 3. The 12 Nov case: a shell with an emptied environment still lands in the throwaway home (read-only look).
room -- env -i /bin/bash -c 'ls -A ~'
assert_contains "$ROUT" .closed-room "env -i bash sees the sentinel in ~"
room -- bash -c 'ls -A ~'
assert_not_contains "$ROUT" Documents "the home shows only the throwaway"

# 4. /tmp, /mnt and /run start empty.
# Started from this repo's folder (not under /tmp), so no kept folder has to be re-created in them.
room_in "$HERE" -- bash -c 'ls -A /tmp | wc -l'
assert_eq "$ROUT" 0 "/tmp is empty at start"
room_in "$HERE" -- bash -c 'ls -A /mnt | wc -l'
assert_eq "$ROUT" 0 "/mnt is empty at start"
room_in "$HERE" -- bash -c 'ls -A /run | wc -l'
assert_eq "$ROUT" 0 "/run is empty at start"

# 5. The cover cannot be taken off.
room -- bash -c 'read -r selfpid _ < /proc/self/stat; echo "self=$selfpid"; grep SigIgn /proc/self/status'
assert_not_contains "$ROUT" "self=1" "the command is a child of the room's first process, not the first process"
case "$ROUT" in *SigIgn:*[0189]) ok ;; *) fail "the command starts with INT or QUIT ignored, so Ctrl-C could not stop it (got: $ROUT)" ;; esac
room -- bash -c 'umount ~ 2>/dev/null; echo rc=$?; test -e ~/.closed-room && echo sentinel-there'
assert_not_contains "$ROUT" "rc=0" "umount of the home is refused"
assert_contains "$ROUT" sentinel-there "the sentinel is there after the umount try"
room -- bash -c 'unshare -Urm umount ~ 2>/dev/null; echo rc=$?; test -e ~/.closed-room && echo sentinel-there'
assert_not_contains "$ROUT" "rc=0" "a nested namespace cannot umount the home"
assert_contains "$ROUT" sentinel-there "the sentinel is still there after a nested umount try"

# 6. No process from outside shows in /proc.
room -- bash -c "test ! -d /proc/$$ && echo hidden"
assert_eq "$ROUT" hidden "the outer shell is not a folder in /proc"
# The command is not the room's first process (that one ignores a plain kill and hides from ancestry walks that stop at pid 1).
room -- bash -c 'kill -TERM $$; echo survived'
assert_not_contains "$ROUT" survived "a plain kill ends the command: it is not pid 1"
room -- bash -c '[ "$$" != 1 ] && echo not-pid-1'
assert_eq "$ROUT" not-pid-1 "the command is not pid 1"

# 7. No route off the machine without --net.
began=$SECONDS
room -- python3 -c 'import socket,sys
s = socket.socket(); s.settimeout(3)
try:
    s.connect(("192.0.2.1", 9)); print("connected")
except OSError:
    print("no route")'
assert_eq "$ROUT" "no route" "a connect to a TEST-NET address fails"
[ $((SECONDS - began)) -le 8 ] && ok || fail "the connect try did not fail fast"

# 8. Guarded write probes: nothing reaches the real home or the real /tmp, by plain shell or by an emptied environment.
P="closed-room-probe-$$"
room -- bash -c 'test -e ~/.closed-room && : > ~/'"$P"
assert_eq "$RRC" 0 "the guarded home write ran inside the room"
[ ! -e "$REAL_HOME/$P" ] && ok || { rm -f "$REAL_HOME/$P"; fail "the real home got the probe file"; }
room -- env -i /bin/bash -c 'test -e ~/.closed-room && : > ~/'"$P-envi"
assert_eq "$RRC" 0 "the guarded write through env -i ran inside the room"
[ ! -e "$REAL_HOME/$P-envi" ] && ok || { rm -f "$REAL_HOME/$P-envi"; fail "the real home got the env -i probe file"; }
room -- bash -c 'test -e ~/.closed-room && : > /tmp/'"$P-tmp"
assert_eq "$RRC" 0 "the guarded /tmp write ran inside the room"
[ ! -e "/tmp/$P-tmp" ] && ok || { rm -f "/tmp/$P-tmp"; fail "the real /tmp got the probe file"; }
room -- env -i /bin/bash -c 'test -e ~/.closed-room && : > /tmp/'"$P-tmp2"
[ ! -e "/tmp/$P-tmp2" ] && ok || { rm -f "/tmp/$P-tmp2"; fail "the real /tmp got the env -i probe file"; }

# 9. A relative walk up from the starting folder reaches the throwaway home, not the real one.
room_in "$HERE" -- bash -c 'h="$(getent passwd "$(id -u)" | cut -d: -f6)"
  while [ "$PWD" != "$h" ] && [ "$PWD" != / ]; do cd ..; done
  echo "at:$PWD"; ls -A'
assert_contains "$ROUT" ".closed-room" "the walk up shows the throwaway home with its sentinel"
assert_contains "$ROUT" "at:$REAL_HOME"$'\n' "the walk up reaches the home path before the root"

# 10. The kept checkout is readable and writable; --ro refuses writes; a linked worktree's main .git is visible and read-only.
room -- bash -c 'git rev-parse --show-toplevel; touch kept-file && test -f kept-file && rm kept-file && echo rw-ok'
assert_contains "$ROUT" "$CK" "the room starts in the scratch checkout"
assert_contains "$ROUT" rw-ok "the kept checkout can be written and read"
RO="$TMP/ro-dir"; mkdir "$RO"; : >"$RO/there"
room --ro "$RO" -- bash -c 'test -e '"$RO"'/there && echo readable; : > '"$RO"'/new 2>/dev/null && echo written'
assert_contains "$ROUT" readable "a --ro folder can be read"
assert_not_contains "$ROUT" written "a --ro folder refuses writes"
[ ! -e "$RO/new" ] && ok || fail "the --ro folder got a file"
RW="$TMP/rw-dir"; mkdir "$RW"
room --rw "$RW" -- bash -c ': > '"$RW"'/new && echo written'
assert_contains "$ROUT" written "a --rw folder can be written"
[ -e "$RW/new" ] && ok || fail "the --rw write did not reach the folder"
room_in "$WT" -- bash -c 'test -r '"$CK"'/.git/HEAD && echo main-git-visible; test -w '"$CK"'/.git && echo main-git-writable; touch wt-file && rm wt-file && echo wt-rw-ok'
assert_contains "$ROUT" main-git-visible "a linked worktree sees the main .git"
assert_not_contains "$ROUT" main-git-writable "the main .git is read-only"
assert_contains "$ROUT" wt-rw-ok "the linked worktree itself is writable"
# The room refuses to keep the real home itself, / or what is above the home.
room --rw "$REAL_HOME" -- true
assert_eq "$RRC" 97 "keeping the real home is refused"
room --rw / -- true
assert_eq "$RRC" 97 "keeping / is refused"

# 11. Exit codes and usage.
room -- bash -c 'exit 7'
assert_eq "$RRC" 7 "the command's exit code is the room's"
MARK="$TMP/should-not-exist"
room touch "$MARK"
assert_eq "$RRC" 2 "a missing -- is a usage error"
[ ! -e "$MARK" ] && ok || fail "a command ran without --"
room --
assert_eq "$RRC" 2 "no command is a usage error"
room --bogus -- touch "$MARK"
assert_eq "$RRC" 2 "an unknown option is a usage error"
[ ! -e "$MARK" ] && ok || fail "a command ran after a usage error"

# 12. Fail closed: when unshare fails, the room exits 97 and the command did not run.
FAKE="$TMP/fakebin"; mkdir "$FAKE"
printf '#!/bin/sh\ncase "$*" in *--help*) echo "  --map-user";; esac\nexit 1\n' >"$FAKE/unshare"
chmod +x "$FAKE/unshare"
RRC=0; ROUT="$(cd "$CK" && PATH="$FAKE:$PATH" timeout 60 "$ROOM" -- touch "$MARK" 2>&1)" || RRC=$?
assert_eq "$RRC" 97 "a failing unshare gives exit 97"
assert_contains "$ROUT" "nothing was run" "the failure line says nothing was run"
[ ! -e "$MARK" ] && ok || fail "the command ran although the room could not be built"
printf '#!/bin/sh\nexit 1\n' >"$FAKE/unshare"
RRC=0; ROUT="$(cd "$CK" && PATH="$FAKE:$PATH" timeout 60 "$ROOM" -- touch "$MARK" 2>&1)" || RRC=$?
assert_eq "$RRC" 97 "an unshare without --map-user gives exit 97"
[ ! -e "$MARK" ] && ok || fail "the command ran with an unshare that cannot map the user"

# 13. The room leaves nothing behind.
mkdir "$TMP/own-tmp"
RRC=0; (cd "$CK" && TMPDIR="$TMP/own-tmp" timeout 60 "$ROOM" -- true) || RRC=$?
assert_eq "$RRC" 0 "a run with its own TMPDIR works"
left="$(ls -A "$TMP/own-tmp" | wc -l)"
assert_eq "$left" 0 "no throwaway folder is left behind"
[ ! -e "$REAL_HOME/.cr-hold" ] && ok || fail "the holding folder was left in the real home"

echo "$checks checks passed, 0 failed"
