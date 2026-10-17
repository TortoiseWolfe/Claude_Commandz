#!/usr/bin/env bash
# openclaw_uid.sh: give OpenClaw's gateway its own uid, and make the drop folder a shared-group data folder.
#
#   openclaw_uid.sh apply              back up, renumber the gateway user, move the gateway to a system unit, fix
#                                      the drop folder, read everything back (exit 1 if any check fails)
#   openclaw_uid.sh verify             the read-back and proofs only, changes nothing
#   openclaw_uid.sh shim               (re)install only the probe shim below; no restart, no uid change
#   openclaw_uid.sh rollback [BACKUP]  one command: old unit, old uid, old ownership, old folder mode
#
# Why this exists. WSL distros share uid 1000 across /mnt/wsl, and the gateway distro sees the main distro's
# Docker bind mounts (repo env files, .aws, .claude). The gateway ran as uid 1000, so file modes protected
# nothing. A gateway at another uid cannot write what uid 1000 owns, and a SYSTEM unit with User= can hide the
# shared mounts with InaccessiblePaths (in a user unit the user can undo that; here it cannot).
#
# The uid. NEW_UID=52100, NEW_GID=52100, drop group ocdrop=52101. Ubuntu's login range is 1000-59999, so the
# account stays an ordinary one for every tool; 52100 sits far from 1000-1999 (the first user of every WSL
# distro, Docker Desktop and the Windows drive mapping all start there), below systemd's dynamic range
# (61184-65519) and nobody (65534), and below the 100000+ subuid block. Preflight proves it unused here and
# as an owner of every entry under the shared Docker mounts.
#
# Runs INSIDE the OpenClawGateway distro as root. The gateway user keeps the name "openclaw", so
# `wsl.exe -d OpenClawGateway -u openclaw -- openclaw ...` and openclaw_harden.sh keep working.
#   wsl.exe -d OpenClawGateway -u root -- bash -s -- apply < THIS_FILE
# Piped through `bash -s`, bash reads this file from stdin line by line, and any program started on the way
# would swallow what is left of it. So the file holds function definitions and one last line only: main runs
# after the whole file is read, with stdin pointed at /dev/null.
#
# The probe shim. OpenClaw Companion decides whether to hand the gateway token to its web chat by running, as the
# distro's default user, `systemctl --user show openclaw-gateway -p MainPID --value` and matching that pid
# against the listener on the port. Once the gateway is a system unit the user manager has no such unit and
# answers 0, so Companion showed "Web Chat Unavailable". /usr/local/bin/systemctl (root-owned, ahead of
# /usr/bin in the PATH wsl.exe gives) answers exactly that one query from the system manager and passes every
# other call straight to the real systemctl, so the unit stays a system unit and nothing else changes.
#
# Every default below can be overridden by an OC_* variable. That is how the staged copy is tested on a
# throwaway user and unit first (tests/test_openclaw_uid_stage.sh); the live run uses the defaults.
# It prints only counts, ids and PASS/FAIL lines: never a path under the shared mounts, a key or a token.
set -uo pipefail

fail=0
check() { # check NAME 0|1 [detail]
  if [ "$2" = 0 ]; then echo "PASS $1 ${3:-}"; else echo "FAIL $1 ${3:-}"; fail=1; fi
}
note() { echo "INFO $*"; }
die() { echo "$*"; exit 1; }

init() {
  cd / || exit 1
  OC_USER="${OC_USER:-openclaw}"
  NEW_UID="${OC_NEW_UID:-52100}"
  NEW_GID="${OC_NEW_GID:-52100}"
  DROP_GROUP="${OC_DROP_GROUP:-ocdrop}"
  DROP_GID="${OC_DROP_GID:-52101}"
  HUB_UID="${OC_HUB_UID:-1000}"            # the main distro's user: owns the Docker mounts and the drop folder
  UNIT="${OC_UNIT:-openclaw-gateway}"
  PORT="${OC_PORT:-18789}"
  DROP_DIR="${OC_DROP_DIR:-/mnt/wsl/ocstage}"
  MOUNTS="${OC_MOUNTS:-/mnt/wsl/docker-desktop-bind-mounts/Debian}"
  HIDE="${OC_HIDE:-/mnt/wsl/docker-desktop-bind-mounts /mnt/wsl/docker-desktop}"
  SOCK_DIRS="${OC_SOCK_DIRS:-/mnt/wsl/docker-desktop-bind-mounts/Debian /mnt/wsl/docker-desktop/shared-sockets/guest-services /mnt/wsl/docker-desktop/shared-sockets/host-services}"
  ROOT="${OC_BACKUP_ROOT:-/var/backups/openclaw-uid}"
  LIVE="${OC_LIVE:-1}"                     # 1: also ask the real openclaw CLI; stage runs set 0
  SYSUNIT="/etc/systemd/system/$UNIT.service"
  TMPF="/etc/tmpfiles.d/$UNIT-dropdir.conf"
  SHIM="${OC_SHIM:-/usr/local/bin/systemctl}"
  [ "$(id -u)" = 0 ] || die "run this as root: wsl.exe -d OpenClawGateway -u root -- bash -s -- MODE < THIS_FILE"
  case "$NEW_UID$NEW_GID$DROP_GID$HUB_UID" in *[!0-9]*|"") die "uid/gid settings must be numbers" ;; esac
  define_py
}

# ---------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------
home_of() { getent passwd "$1" | cut -d: -f6; }
uid_of() { getent passwd "$1" | cut -d: -f3; }
gid_of() { getent passwd "$1" | cut -d: -f4; }

# run the user's systemd manager commands from root
usr_sc() { # usr_sc UID args...
  local uid=$1; shift
  runuser -u "$OC_USER" -- env "XDG_RUNTIME_DIR=/run/user/$uid" "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" systemctl --user "$@"
}

# as_ids UID GID[,GID...] -- cmd...   (GIDs "-" means none)
as_ids() {
  local u=$1 g=$2; shift 2
  if [ "$g" = "-" ]; then setpriv --reuid="$u" --regid="$u" --clear-groups "$@"
  else setpriv --reuid="$u" --regid="${g%%,*}" --groups="$g" "$@"; fi
}

define_py() {
# Python that answers "can this uid write here?" for a list of paths on stdin. Prints
#   total=N writable=N probes=N created=N
# `access` is the kernel's answer; `probes` are real create attempts in the directories it said no to
# (a probe file that does get created is removed and counted, which makes the check fail).
read -r -d '' WRITE_PROBE_PY <<'PY' || true
import os, sys
paths = [l.rstrip("\n") for l in sys.stdin if l.strip()]
writable = probes = created = 0
for p in paths:
    if os.access(p, os.W_OK):
        writable += 1
        continue
    if probes < 12 and os.path.isdir(p) and not os.path.islink(p):
        probes += 1
        q = os.path.join(p, ".ocuid-probe-%d" % os.getpid())
        try:
            fd = os.open(q, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd); os.unlink(q); created += 1
        except OSError:
            pass
print("total=%d writable=%d probes=%d created=%d" % (len(paths), writable, probes, created))
PY

# Python that lists the shared-mount entries a uid can write (top level, no recursion).
read -r -d '' LIST_WRITABLE_PY <<'PY' || true
import os, sys
root = sys.argv[1]
try: names = sorted(os.listdir(root))
except OSError: sys.exit(0)
for n in names:
    p = os.path.join(root, n)
    if os.access(p, os.W_OK):
        print(p)
PY

# Python that counts sockets a uid can write (the Docker engine socket is the one that matters).
read -r -d '' SOCKET_PROBE_PY <<'PY' || true
import os, stat, sys
n = w = 0
for d in sys.argv[1:]:
    try: names = os.listdir(d)
    except OSError: continue
    for name in names:
        p = os.path.join(d, name)
        try:
            if stat.S_ISSOCK(os.lstat(p).st_mode):
                n += 1
                if os.access(p, os.W_OK): w += 1
        except OSError:
            pass
print("sockets=%d writable=%d" % (n, w))
PY
}

# the exact set the old uid could write, as the main distro's uid sees the shared mounts
list_hub_writable() { as_ids "$HUB_UID" - python3 -I -c "$LIST_WRITABLE_PY" "$MOUNTS" 2>/dev/null; }

kv() { # kv key "total=1 writable=2" -> value
  tr ' ' '\n' <<<"$2" | sed -n "s/^$1=//p"
}

install_probe_shim() {
  echo "== probe shim $SHIM"
  local real=/usr/bin/systemctl tmp="$SHIM.ocuid.tmp"
  [ -x "$real" ] || die "no $real to pass calls on to"
  cat > "$tmp" <<SH
#!/bin/sh
# written by openclaw_uid.sh (shim mode). Companion's provenance probe asks the user manager for this unit's
# MainPID; the unit is a system unit, so answer that one query from the system manager. Everything else is
# passed through to the real systemctl untouched.
case "\$*" in
  "--user show $UNIT -p MainPID --value"|"--user show $UNIT.service -p MainPID --value")
    exec $real show $UNIT.service -p MainPID --value ;;
esac
exec $real "\$@"
SH
  chown root:root "$tmp" && chmod 755 "$tmp" && mv -f "$tmp" "$SHIM"
  echo "installed: $(stat -c '%a %U:%G' "$SHIM")"
}

main_pid() { systemctl show "$UNIT.service" -p MainPID --value 2>/dev/null; }

wait_listen() { # wait_listen SECONDS
  local i; for ((i = 0; i < $1; i++)); do
    ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN && return 0
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------------------------------
do_verify() {
  local out line n m
  echo "== identity"
  local oldid="$HUB_UID"
  [ -s "$ROOT/latest/meta.env" ] && oldid="$(sed -n 's/^OLD_UID=//p' "$ROOT/latest/meta.env")"
  local pw; pw="$(getent passwd "$OC_USER")"
  [ "$(cut -d: -f3 <<<"$pw")" = "$NEW_UID" ] && [ "$(cut -d: -f4 <<<"$pw")" = "$NEW_GID" ]; check user.uid_gid $? "$OC_USER uid=$(cut -d: -f3 <<<"$pw") gid=$(cut -d: -f4 <<<"$pw") (want $NEW_UID/$NEW_GID)"
  [ -z "$(getent passwd "$oldid")" ]; check user.old_uid_unassigned $? "uid $oldid has no account in this distro"
  local groups; groups="$(id -G "$OC_USER" 2>/dev/null | tr ' ' '\n' | sort -n | paste -sd' ')"
  [ "$groups" = "$(printf '%s\n' "$NEW_GID" "$DROP_GID" | sort -n | paste -sd' ')" ]; check user.groups $? "gids: $groups (want $NEW_GID $DROP_GID only)"
  [ "$(getent group "$DROP_GROUP" | cut -d: -f3)" = "$DROP_GID" ]; check group.drop $? "$DROP_GROUP gid=$(getent group "$DROP_GROUP" | cut -d: -f3)"
  passwd -S "$OC_USER" >/dev/null 2>&1; check user.account_valid $?

  echo "== ownership"
  local home; home="$(home_of "$OC_USER")"
  n="$(find / -xdev \( -uid "$oldid" -o -gid "$oldid" \) 2>/dev/null | wc -l)"
  [ "$n" = 0 ]; check state.no_files_left_at_old_ids $? "$n entries on / still owned by id $oldid"
  local bad; bad="$(find "$home" -xdev \( -not -uid "$NEW_UID" -o -not -gid "$NEW_GID" \) 2>/dev/null | head -3 | wc -l)"
  [ "$bad" = 0 ]; check state.home_owned_by_new_ids $? "$home"

  echo "== system unit"
  local show; show="$(systemctl show "$UNIT.service" -p User -p Group -p FragmentPath -p ActiveState -p UnitFileState -p NoNewPrivileges -p InaccessiblePaths -p MainPID 2>&1)"
  grep -qx "FragmentPath=$SYSUNIT" <<<"$show"; check unit.is_system_unit $? "$SYSUNIT"
  grep -qx "User=$OC_USER" <<<"$show" && grep -qx "Group=$OC_USER" <<<"$show"; check unit.User $? "User=$OC_USER Group=$OC_USER"
  grep -qx "ActiveState=active" <<<"$show" && grep -qx "UnitFileState=enabled" <<<"$show"; check unit.active_and_enabled $?
  grep -qx "NoNewPrivileges=yes" <<<"$show"; check unit.no_new_privs $?
  local hide_ok=0 hp; for hp in $HIDE; do grep -q "^InaccessiblePaths=.*$hp" <<<"$show" || hide_ok=1; done
  check unit.inaccessible_paths $hide_ok "$HIDE"
  local ucount=0 uf="$home/.config/systemd/user/$UNIT.service"
  [ -e "$uf" ] && ucount=1
  [ -e "$home/.config/systemd/user/default.target.wants/$UNIT.service" ] && ucount=1
  [ "$ucount" = 0 ]; check unit.no_competing_user_unit $? "no $UNIT.service in the user's systemd dir"

  echo "== process"
  local pid; pid="$(main_pid)"
  if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -r "/proc/$pid/status" ]; then
    local u; u="$(awk '/^Uid:/{print $2,$3,$4,$5}' "/proc/$pid/status")"
    [ "$u" = "$NEW_UID $NEW_UID $NEW_UID $NEW_UID" ]; check process.uid $? "main pid $pid real/eff/saved/fs uid: $u"
    local cg bad_pids=0 total=0 p
    cg="$(systemctl show "$UNIT.service" -p ControlGroup --value)"
    for p in $(cat "/sys/fs/cgroup$cg/cgroup.procs" 2>/dev/null); do
      total=$((total + 1))
      [ "$(awk '/^Uid:/{print $2}' "/proc/$p/status" 2>/dev/null)" = "$NEW_UID" ] || bad_pids=$((bad_pids + 1))
    done
    [ "$total" -gt 0 ] && [ "$bad_pids" = 0 ]; check process.cgroup_all_uid $? "$total processes in the unit, $bad_pids not at uid $NEW_UID"
    ss -ltnp "sport = :$PORT" 2>/dev/null | grep -q "pid=$pid,"; check process.listens $? "port $PORT held by the unit's main pid"
    local nonloop; nonloop="$(ss -ltn "sport = :$PORT" 2>/dev/null | awk 'NR>1{print $4}' | grep -vc -E '^(127\.0\.0\.1|\[::1\]):')"
    [ "$nonloop" = 0 ]; check process.loopback_only $? "port $PORT listeners off loopback: $nonloop"
  else
    check process.uid 1 "no running main pid"
  fi

  echo "== Companion provenance probe"
  local shim_pid; shim_pid="$(runuser -u "$OC_USER" -- "$SHIM" --user show "$UNIT" -p MainPID --value 2>/dev/null)"
  [ -n "$pid" ] && [ "$pid" != 0 ] && [ "$shim_pid" = "$pid" ]; check probe.shim_answers_main_pid $? "shim says '$shim_pid', system manager says '$pid'"
  [ "$(stat -c '%U %a' "$SHIM" 2>/dev/null)" = "root 755" ]; check probe.shim_root_owned $? "$SHIM"
  runuser -u "$OC_USER" -- "$SHIM" --version >/dev/null 2>&1; check probe.shim_passes_other_calls $?

  echo "== drop folder $DROP_DIR"
  local st; st="$(stat -c '%a %u %g' "$DROP_DIR" 2>&1)"
  [ "$st" = "1770 $HUB_UID $DROP_GID" ]; check drop.mode_owner_group $? "$st (want 1770 $HUB_UID $DROP_GID)"
  grep -q "$DROP_DIR 1770 $HUB_UID $DROP_GID" "$TMPF" 2>/dev/null; check drop.recreated_at_boot $? "$TMPF"
  local t="$DROP_DIR/.ocuid-test-$$" tm="$DROP_DIR/.ocuid-hub-$$"
  as_ids "$HUB_UID" - bash -c ": > '$tm' && chmod 644 '$tm'" 2>/dev/null
  as_ids "$NEW_UID" "$NEW_GID,$DROP_GID" bash -c ": > '$t'" 2>/dev/null; check drop.gateway_can_write $?
  as_ids "$NEW_UID" "$NEW_GID,$DROP_GID" bash -c "cat '$tm' >/dev/null" 2>/dev/null; check drop.gateway_can_read_hub_file $?
  as_ids "$NEW_UID" "$NEW_GID,$DROP_GID" bash -c "rm -f '$tm'" 2>/dev/null; [ -e "$tm" ]; check drop.sticky_gateway_cannot_delete_hub_file $?
  as_ids "$NEW_UID" "$NEW_GID,$DROP_GID" bash -c "echo x >> '$tm'" 2>/dev/null; [ $? != 0 ]; check drop.gateway_cannot_modify_hub_file $?
  as_ids 59999 - bash -c "ls '$DROP_DIR' >/dev/null 2>&1 || exit 7; : > '$DROP_DIR/.ocuid-stranger-$$'" >/dev/null 2>&1; [ $? = 7 ]; check drop.stranger_locked_out $? "an unrelated uid can neither list nor create"
  as_ids "$HUB_UID" - bash -c "rm -f '$t' '$tm'" 2>/dev/null; [ ! -e "$t" ] && [ ! -e "$tm" ]; check drop.hub_can_clean_up $?
  rm -f "$DROP_DIR/.ocuid-stranger-$$"

  echo "== shared Docker mounts ($MOUNTS)"
  local list; list="$(mktemp)"; chmod 600 "$list"
  list_hub_writable > "$list"
  [ -s "$ROOT/latest/baseline.list" ] && cat "$ROOT/latest/baseline.list" >> "$list"
  sort -u "$list" -o "$list"
  m="$(wc -l < "$list")"
  [ "$m" -gt 0 ]; check mounts.sample_not_empty $? "$m entries the old uid could write (union of the apply-time baseline and now)"
  out="$(as_ids "$NEW_UID" "$NEW_GID,$DROP_GID" python3 -I -c "$WRITE_PROBE_PY" < "$list" 2>&1)"
  note "ordinary shell of the new uid: $out  (drvfs/9p mounts show open modes; the unit hides them, next line)"
  if [ -n "$pid" ] && [ "$pid" != 0 ]; then
    out="$(nsenter -t "$pid" -m -- setpriv --reuid="$NEW_UID" --regid="$NEW_GID" --groups="$NEW_GID,$DROP_GID" python3 -I -c "$WRITE_PROBE_PY" < "$list" 2>&1)"
    n="$(kv writable "$out")"; local made; made="$(kv created "$out")"
    [ "$n" = 0 ] && [ "$made" = 0 ] && [ "$(kv total "$out")" = "$m" ]; check mounts.gateway_namespace_writable $? "$out"
    out="$(nsenter -t "$pid" -m -- setpriv --reuid="$NEW_UID" --regid="$NEW_GID" --groups="$NEW_GID,$DROP_GID" python3 -I -c "$SOCKET_PROBE_PY" $SOCK_DIRS 2>&1)"
    [ "$(kv writable "$out")" = 0 ]; check sockets.gateway_namespace_writable $? "$out"
  else
    check mounts.gateway_namespace_writable 1 "no gateway pid to enter"
  fi
  rm -f "$list"

  echo "== rollback material"
  [ -s "$ROOT/latest/meta.env" ] && [ -s "$ROOT/latest/passwd" ]; check backup.present $? "$(readlink "$ROOT/latest" 2>/dev/null)"

  if [ "$LIVE" = 1 ]; then
    echo "== openclaw CLI as $OC_USER"
    local gs; gs="$(runuser -u "$OC_USER" -- timeout 90 openclaw gateway status 2>&1 | sed -E 's/[A-Za-z0-9_-]{24,}/<masked>/g' | head -12)"
    echo "$gs" | sed 's/^/   /'
    runuser -u "$OC_USER" -- timeout 90 openclaw config validate >/dev/null 2>&1; check openclaw.config_validates $?
  fi
}

# ---------------------------------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------------------------------
preflight() {
  echo "== 0 preflight"
  id "$OC_USER" >/dev/null 2>&1 || die "no user $OC_USER here"
  OLD_UID="$(uid_of "$OC_USER")"; OLD_GID="$(gid_of "$OC_USER")"
  if [ "$OLD_UID" = "$NEW_UID" ]; then die "$OC_USER is already uid $NEW_UID; run verify"; fi
  for t in nsenter setpriv python3 runuser systemd-tmpfiles ss groupadd gpasswd; do command -v "$t" >/dev/null || die "missing tool: $t"; done
  [ -z "$(getent passwd "$NEW_UID")" ] && [ -z "$(getent group "$NEW_GID")" ] && [ -z "$(getent group "$DROP_GID")" ] || die "uid/gid $NEW_UID/$NEW_GID/$DROP_GID already in use here"
  [ -z "$(getent group "$DROP_GROUP")" ] || die "group $DROP_GROUP already exists"
  local n; n="$(find / -xdev \( -uid "$NEW_UID" -o -gid "$NEW_GID" -o -gid "$DROP_GID" \) 2>/dev/null | head -1 | wc -l)"
  [ "$n" = 0 ] || die "files on / already carry the new ids; refusing, a rollback by id would be ambiguous"
  n="$(find "$MOUNTS" -maxdepth 1 \( -uid "$NEW_UID" -o -uid "$DROP_GID" \) 2>/dev/null | head -1 | wc -l)"
  [ "$n" = 0 ] || die "an entry under the shared Docker mounts already belongs to the new ids"
  USER_UNIT="$(home_of "$OC_USER")/.config/systemd/user/$UNIT.service"
  [ -f "$USER_UNIT" ] || die "no user unit at $USER_UNIT: nothing to move (apply already ran? run verify)"
  [ ! -e "$SYSUNIT" ] || die "$SYSUNIT already exists"
  command -v ss >/dev/null && ss -ltn "sport = :$PORT" | grep -q LISTEN || note "nothing listens on $PORT now (will start it)"
  echo "PASS preflight user=$OC_USER uid=$OLD_UID gid=$OLD_GID -> $NEW_UID/$NEW_GID, drop group $DROP_GROUP=$DROP_GID"
}

do_backup() {
  echo "== 1 backup"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  BK="$ROOT/$stamp"
  mkdir -p "$ROOT" && chmod 700 "$ROOT" && mkdir -m 700 "$BK" || die "cannot create $BK"
  cp -p /etc/passwd /etc/group /etc/shadow /etc/gshadow "$BK/" || die "cannot copy account files"
  cp -p "$USER_UNIT" "$BK/user-unit.service"
  { printf 'OLD_UID=%q\n' "$OLD_UID"; printf 'OLD_GID=%q\n' "$OLD_GID"; printf 'NEW_UID=%q\n' "$NEW_UID"; printf 'NEW_GID=%q\n' "$NEW_GID"
    printf 'DROP_GROUP=%q\n' "$DROP_GROUP"; printf 'DROP_GID=%q\n' "$DROP_GID"; printf 'OC_USER=%q\n' "$OC_USER"; printf 'UNIT=%q\n' "$UNIT"
    printf 'USER_UNIT=%q\n' "$USER_UNIT"; printf 'DROP_DIR=%q\n' "$DROP_DIR"; printf 'SYSUNIT=%q\n' "$SYSUNIT"; printf 'TMPF=%q\n' "$TMPF"
    printf 'USER_UNIT_ENABLED=%q\n' "$(usr_sc "$OLD_UID" is-enabled "$UNIT.service" 2>/dev/null | head -1)"
    printf 'USER_UNIT_ACTIVE=%q\n' "$(usr_sc "$OLD_UID" is-active "$UNIT.service" 2>/dev/null | head -1)"
    if [ -e "$DROP_DIR" ]; then printf 'DROP_STAT=%q\n' "$(stat -c '%a %u %g' "$DROP_DIR")"; else echo "DROP_STAT=absent"; fi
  } > "$BK/meta.env"
  chmod 600 "$BK"/*
  list_hub_writable > "$BK/baseline.list"; chmod 600 "$BK/baseline.list"
  ln -sfn "$BK" "$ROOT/latest"
  echo "backup: $BK ($(wc -l < "$BK/baseline.list") writable shared-mount entries recorded as the baseline)"
  # shellcheck disable=SC1091
  . "$BK/meta.env"
}

stop_old() {
  echo "== 2 stop the user-level gateway"
  usr_sc "$OLD_UID" disable --now "$UNIT.service" 2>&1 | tail -2
  mv "$USER_UNIT" "$USER_UNIT.moved-by-uid-script" && echo "moved the user unit aside (kept in $BK too)"
  usr_sc "$OLD_UID" daemon-reload 2>/dev/null
  systemctl stop "user@$OLD_UID.service" 2>&1 | tail -2
  local i; for ((i = 0; i < 30; i++)); do ss -ltn "sport = :$PORT" | grep -q LISTEN || break; sleep 1; done
  ss -ltn "sport = :$PORT" | grep -q LISTEN && die "port $PORT still held after stopping the user unit; nothing else changed yet except the unit move. Run: rollback"
  echo "user manager for uid $OLD_UID stopped; port $PORT free"
}

renumber() {
  echo "== 3 renumber $OC_USER $OLD_UID -> $NEW_UID (account files edited atomically, then read back)"
  python3 -I - "$OC_USER" "$OLD_UID" "$OLD_GID" "$NEW_UID" "$NEW_GID" <<'PY' || die "account edit failed; run: rollback"
import os, sys
user, ou, og, nu, ng = sys.argv[1:6]
def rewrite(path, fn):
    st = os.stat(path)
    out = "\n".join(fn(l) for l in open(path).read().split("\n"))
    tmp = path + ".ocuid.tmp"
    with open(tmp, "w") as f: f.write(out)
    os.chmod(tmp, st.st_mode & 0o7777); os.chown(tmp, st.st_uid, st.st_gid); os.replace(tmp, path)
def pw(l):
    p = l.split(":")
    if len(p) > 3 and p[0] == user and p[2] == ou and p[3] == og: p[2], p[3] = nu, ng
    return ":".join(p)
def gr(l):
    p = l.split(":")
    if len(p) > 2 and p[0] == user and p[2] == og: p[2] = ng
    return ":".join(p)
rewrite("/etc/passwd", pw); rewrite("/etc/group", gr)
PY
  [ "$(uid_of "$OC_USER")" = "$NEW_UID" ] && [ "$(gid_of "$OC_USER")" = "$NEW_GID" ] || die "read-back of the account failed; run: rollback"
  groupadd -g "$DROP_GID" "$DROP_GROUP" && gpasswd -a "$OC_USER" "$DROP_GROUP" >/dev/null || die "could not create $DROP_GROUP; run: rollback"
  echo "account now: $(getent passwd "$OC_USER" | cut -d: -f1,3,4)  groups: $(id -G "$OC_USER")"
}

chown_state() {
  echo "== 4 move ownership of everything on / from id $OLD_UID to $NEW_UID (state dir, tool tree, logs, tmp)"
  local nu ng
  nu="$(find / -xdev -uid "$OLD_UID" 2>/dev/null | wc -l)"; ng="$(find / -xdev -gid "$OLD_GID" 2>/dev/null | wc -l)"
  find / -xdev -uid "$OLD_UID" -exec chown -h "$NEW_UID" {} + 2>/dev/null
  find / -xdev -gid "$OLD_GID" -exec chgrp -h "$NEW_GID" {} + 2>/dev/null
  echo "chowned $nu entries by uid and $ng by gid"
}

make_drop_and_unit() {
  echo "== 5 drop folder at 1770, recreated at every boot"
  printf '# written by openclaw_uid.sh: the drop folder is data only. Hub owns it, the gateway group works in it, nobody else.\nd %s 1770 %s %s -\n' "$DROP_DIR" "$HUB_UID" "$DROP_GID" > "$TMPF"
  chmod 644 "$TMPF"
  systemd-tmpfiles --create "$TMPF" 2>&1 | tail -2
  echo "folder: $(stat -c '%a owner=%u group=%g' "$DROP_DIR")"

  echo "== 6 system unit with User=$OC_USER"
  python3 -I - "$BK/user-unit.service" "$SYSUNIT" "$OC_USER" "$HIDE" <<'PY' || die "could not build the system unit; run: rollback"
import sys
src, dst, user, hide = sys.argv[1:5]
out, section, seen_service = [], None, False
out.append("# written by openclaw_uid.sh from the user unit: same ExecStart and environment, run as %s by the system manager." % user)
out.append("# InaccessiblePaths hides the shared Docker mounts from the gateway; as a system unit the gateway user cannot undo it.")
for line in open(src).read().splitlines():
    if line.startswith("["):
        section = line.strip()
    if section == "[Install]" and line.startswith("WantedBy="):
        line = "WantedBy=multi-user.target"
    out.append(line)
    if line.strip() == "[Service]" and not seen_service:
        seen_service = True
        out += ["User=" + user, "Group=" + user, "NoNewPrivileges=yes",
                "InaccessiblePaths=" + " ".join("-" + p for p in hide.split()),
                "Environment=OPENCLAW_SERVICE_REPAIR_POLICY=external"]
open(dst, "w").write("\n".join(out) + "\n")
PY
  chmod 644 "$SYSUNIT"
  systemctl daemon-reload && systemctl enable "$UNIT.service" 2>&1 | tail -1
  systemctl start "$UNIT.service" 2>&1 | tail -3
  wait_listen 120 && echo "gateway is listening on $PORT" || echo "gateway did not listen on $PORT within 120 s (see: journalctl -u $UNIT)"
  install_probe_shim
}

do_apply() {
  preflight
  do_backup
  stop_old
  renumber
  chown_state
  make_drop_and_unit
  do_verify
  echo
  if [ "$fail" = 0 ]; then echo "ALL CHECKS PASS. Roll back with: openclaw_uid.sh rollback   (restores $BK)"; else echo "SOME CHECKS FAILED. Roll back with: openclaw_uid.sh rollback   (restores $BK)"; fi
  stale="$(ps -eo uid=,pid=,comm= | awk -v u="$OLD_UID" '$1==u{print $2":"$3}' | paste -sd' ')"
  [ -z "$stale" ] || note "processes still running at the old uid $OLD_UID (started before the change; they restart as the new uid): $stale"
  exit "$fail"
}

# ---------------------------------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------------------------------
do_rollback() {
  local bk="${1:-$ROOT/latest}"
  [ -d "$bk" ] && [ -s "$bk/meta.env" ] || die "no backup at $bk"
  bk="$(readlink -f "$bk")"
  # shellcheck disable=SC1091
  . "$bk/meta.env"
  echo "== rollback to $bk (uid $NEW_UID -> $OLD_UID, system unit -> user unit)"
  systemctl disable --now "$UNIT.service" 2>&1 | tail -1
  rm -f "$SYSUNIT" "$TMPF" "$SHIM"; systemctl daemon-reload
  local i; for ((i = 0; i < 30; i++)); do ss -ltn "sport = :$PORT" | grep -q LISTEN || break; sleep 1; done

  python3 -I - "$OC_USER" "$OLD_UID" "$OLD_GID" "$NEW_UID" "$NEW_GID" <<'PY' || die "account restore failed; the copies are in $bk"
import os, sys
user, ou, og, nu, ng = sys.argv[1:6]
def rewrite(path, fn):
    st = os.stat(path)
    out = "\n".join(fn(l) for l in open(path).read().split("\n"))
    tmp = path + ".ocuid.tmp"
    with open(tmp, "w") as f: f.write(out)
    os.chmod(tmp, st.st_mode & 0o7777); os.chown(tmp, st.st_uid, st.st_gid); os.replace(tmp, path)
def pw(l):
    p = l.split(":")
    if len(p) > 3 and p[0] == user and p[2] == nu and p[3] == ng: p[2], p[3] = ou, og
    return ":".join(p)
def gr(l):
    p = l.split(":")
    if len(p) > 2 and p[0] == user and p[2] == ng: p[2] = og
    return ":".join(p)
rewrite("/etc/passwd", pw); rewrite("/etc/group", gr)
PY
  getent group "$DROP_GROUP" >/dev/null && groupdel "$DROP_GROUP"
  find / -xdev -uid "$NEW_UID" -exec chown -h "$OLD_UID" {} + 2>/dev/null
  find / -xdev -gid "$NEW_GID" -exec chgrp -h "$OLD_GID" {} + 2>/dev/null
  if [ "$DROP_STAT" = absent ]; then rmdir "$DROP_DIR" 2>/dev/null
  else read -r m u g <<<"$DROP_STAT"; chown "$u:$g" "$DROP_DIR" && chmod "$m" "$DROP_DIR"; fi
  echo "account: $(getent passwd "$OC_USER" | cut -d: -f1,3,4)   folder: $(stat -c '%a %u:%g' "$DROP_DIR" 2>/dev/null || echo absent)"

  [ -e "$USER_UNIT.moved-by-uid-script" ] && rm -f "$USER_UNIT.moved-by-uid-script"
  cp -p "$bk/user-unit.service" "$USER_UNIT"
  systemctl start "user@$OLD_UID.service" 2>&1 | tail -1
  for ((i = 0; i < 20; i++)); do [ -S "/run/user/$OLD_UID/bus" ] && break; sleep 1; done
  usr_sc "$OLD_UID" daemon-reload
  if [ "$USER_UNIT_ENABLED" = enabled ]; then usr_sc "$OLD_UID" enable "$UNIT.service" 2>&1 | tail -1; fi
  if [ "$USER_UNIT_ACTIVE" = active ]; then usr_sc "$OLD_UID" start "$UNIT.service" 2>&1 | tail -2; wait_listen 120 && echo "gateway is listening on $PORT again (user unit)" || echo "gateway did not listen on $PORT within 120 s"; fi
  local left; left="$(find / -xdev \( -uid "$NEW_UID" -o -gid "$NEW_GID" \) 2>/dev/null | wc -l)"
  [ "$left" = 0 ]; check rollback.no_files_at_new_ids $? "$left entries"
  [ "$(uid_of "$OC_USER")" = "$OLD_UID" ]; check rollback.uid_restored $?
  [ ! -e "$SYSUNIT" ]; check rollback.system_unit_removed $?
  [ -f "$USER_UNIT" ] && cmp -s "$USER_UNIT" "$bk/user-unit.service"; check rollback.user_unit_restored $?
  exit "$fail"
}

main() {
  MODE="${1:-}"
  init
  case "$MODE" in
    apply) do_apply ;;
    verify) do_verify; exit "$fail" ;;
    shim) install_probe_shim; exit 0 ;;
    rollback) do_rollback "${2:-}" ;;
    *) echo "usage: openclaw_uid.sh apply|verify|shim|rollback [BACKUP_DIR]"; exit 2 ;;
  esac
}

main "$@" </dev/null; exit $?
