#!/usr/bin/env python3
"""wsl_root_check.py: the root-side repair for "Windows launching from WSL stopped working" (issue 368).

Runs AS ROOT inside the main WSL distro, started from Windows (which needs no interop):

    wsl.exe -d Debian -u root -- python3 -I /usr/local/lib/afa-root-check/wsl_root_check.py

(windows/AFA-WslRootCheck.xml does this every 15 minutes; windows/install-wsl-root-check.sh copies this file and wsl_reserve.py
into that root-owned folder and registers the task.) Root never runs code from a folder a person's own processes can write: a
copy in the home folder would let any process of that person become root within 15 minutes. The price is that a changed script
takes effect only when install-wsl-root-check.sh is run again.

Why it exists (10 Nov 2026): after a restart, the OpenClaw tray force-terminated the gateway distro three times and the
binfmt table lost its WSLInterop entry (and Docker's). Without that entry no Linux process can start a Windows program, so
nothing that runs from WSL could repair it, and it stayed broken until a person re-registered it. The cause is not one setting
an update can reset, so this repairs the result: it checks, and fixes only what is missing.

Checks, in this order (the first one is what every later Windows call needs):
  1. interop     /proc/sys/fs/binfmt_misc is mounted and holds WSLInterop; registers it when missing.
  2. gateway     when the OpenClawGateway distro is ALREADY running (never started, never terminated): systemd-binfmt.service
                 stays masked and every binfmt.d file is pointed at /dev/null, so no unit can rewrite the table.
  3. sysctl      the memory reserve wsl_bridge_watchdog.py keeps (it needs root; this process is root, so no interop is needed).
  4. tasks       \\AFA\\OpenClawStatus and \\AFA\\TowerKeeper (whichever exist) are re-enabled when found Disabled; OpenClawStatus
                 only when the gateway container runs or step 2 just saw the distro running with the mask in place (otherwise its
                 refresh could boot the distro unprotected), so it stays Disabled until one of those holds.
  5. docker      Docker Desktop runs but the Linux docker command does not answer on two checks in a row: restart Docker Desktop
                 through its own "docker desktop restart", only when no container runs, at most once an hour.

Anything it changes is one line in <home>/.claude/state/wsl-root-check.log and in the recovery record (the newest one when it is
under an hour old, else a new one). A run that changes nothing writes only wsl-root-check.last there. The state folder is found
as /home/*/.claude/state (a real folder owned by a person, never root); every file written into it is written with that owner's
identity (the effective user and group are switched while it works, then back), so a link planted in that folder is followed with
the owner's rights only and nothing is ever chown-ed.
Exit 0 when nothing is left wrong, 1 when a problem could not be repaired.

Test hooks (environment): ROOTCHECK_STATE (state folder), ROOTCHECK_BINFMT, ROOTCHECK_WSL, ROOTCHECK_SCHTASKS,
ROOTCHECK_TASKLIST, ROOTCHECK_DOCKER, ROOTCHECK_DOCKER_WIN. No machine or user name is written here.
"""
import argparse
import datetime
import fcntl
import os
import re
import stat
import subprocess
import contextlib
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wsl_reserve as w  # noqa: E402  (beside this file; standard library only)

WSL_INTEROP_ENTRY = ":WSLInterop:M::MZ::/init:PF"
GATEWAY = "OpenClawGateway"
KEEPER_TASKS = ["\\AFA\\OpenClawStatus", "\\AFA\\TowerKeeper"]
# A task that can boot the gateway distro (openclaw_status_refresh.sh asks the gateway through openclaw_gateway.py, which boots the distro
# when no container answers) is re-enabled only while that is safe: the gateway container runs, or this run saw the distro running with
# systemd-binfmt.service masked. TowerKeeper is not guarded: it is the keeper-only variant for a machine that never touches the gateway.
GATEWAY_GUARDED_TASKS = ["\\AFA\\OpenClawStatus"]
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
DOCKER_RESTART_GAP_S = 3600
DOCKER_FAILS_NEEDED = 2

# Runs inside the gateway distro as root. Idempotent: prints "CHANGED ..." for each thing it repaired and "PROBLEM ..." for
# anything it could not. ROOT_PREFIX and SYSTEMCTL exist for the tests only.
GATEWAY_SCRIPT = r"""
R="${ROOT_PREFIX:-}"
SC="${SYSTEMCTL:-systemctl}"
st=$($SC is-enabled systemd-binfmt.service 2>/dev/null || true)
if [ "$st" != masked ]; then
  if $SC mask systemd-binfmt.service >/dev/null 2>&1; then echo "CHANGED systemd-binfmt.service masked (was ${st:-unknown})"; else echo "PROBLEM could not mask systemd-binfmt.service"; fi
  st=$($SC is-enabled systemd-binfmt.service 2>/dev/null || true)
fi
[ "$st" = masked ] && echo "MASKED"
mkdir -p "$R/etc/binfmt.d"
names=""
for f in "$R"/usr/lib/binfmt.d/*.conf "$R"/etc/binfmt.d/*.conf; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  names="$names $(basename "$f")"
done
for n in $names; do
  t="$R/etc/binfmt.d/$n"
  if [ -L "$t" ] && [ "$(readlink "$t")" = /dev/null ]; then continue; fi
  if [ -e "$t" ] || [ -L "$t" ]; then mv -f "$t" "$t.disabled" || { echo "PROBLEM could not move $n aside"; continue; }; fi
  if ln -s /dev/null "$t"; then echo "CHANGED binfmt.d/$n pointed at /dev/null"; else echo "PROBLEM could not neutralize binfmt.d/$n"; fi
done
exit 0
"""


def clock12(t):
    return datetime.datetime.fromtimestamp(t).strftime("%-I:%M %p")


def decode(data):
    """wsl.exe -l prints UTF-16; most other output is plain text."""
    if b"\x00" in data:
        try:
            return data.decode("utf-16-le", "replace").replace("﻿", "")
        except Exception:
            pass
    return data.decode("utf-8", "replace")


class Ctx:
    def __init__(self, state, binfmt="/proc/sys/fs/binfmt_misc", wsl="wsl.exe", schtasks="schtasks.exe", tasklist="tasklist.exe",
                 docker="docker", docker_win="docker.exe", run=None, write_register=None, now=time.time, proc="",
                 gateway_env=None):
        self.state, self.binfmt, self.wsl, self.schtasks = state, binfmt, wsl, schtasks
        self.tasklist, self.docker, self.docker_win = tasklist, docker, docker_win
        self._run = run or self._real_run
        self.write_register = write_register or self._real_write_register
        self.now = now
        self.proc = proc  # prefix for /proc/sys paths (tests)
        self.gateway_env = gateway_env or {}
        self.mask_proven = False  # set by check_gateway when it saw the distro running with the mask in place

    def run(self, argv, timeout=30):
        """-> (returncode, text). A missing program or a timeout is (127, '') / (124, '')."""
        return self._run(argv, timeout)

    @staticmethod
    def _real_run(argv, timeout):
        try:
            r = subprocess.run(argv, capture_output=True, timeout=timeout, cwd="/")
            return r.returncode, decode(r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError:
            return 127, ""

    @staticmethod
    def _real_write_register(path, entry):
        with open(path, "w") as f:
            f.write(entry)


def check_interop(ctx):
    changes, problems = [], []
    d = ctx.binfmt
    if not os.path.exists(os.path.join(d, "register")):
        rc, _ = ctx.run(["mount", "-t", "binfmt_misc", "binfmt_misc", d], 20)
        if rc != 0 or not os.path.exists(os.path.join(d, "register")):
            return changes, ["binfmt_misc is not mounted and mounting it failed"]
        changes.append("binfmt_misc mounted")
    if any(os.path.exists(os.path.join(d, n)) for n in ("WSLInterop", "WSLInterop-late")):
        return changes, problems
    try:
        ctx.write_register(os.path.join(d, "register"), WSL_INTEROP_ENTRY)
    except OSError as e:
        return changes, problems + ["WSLInterop is missing and registering it failed (%s)" % e.strerror]
    if os.path.exists(os.path.join(d, "WSLInterop")):
        changes.append("WSLInterop was missing from binfmt_misc: registered it")
    else:
        problems.append("WSLInterop is missing and the register write did not create it")
    return changes, problems


def running_distros(ctx):
    rc, out = ctx.run([ctx.wsl, "-l", "--running", "-q"], 20)
    if rc != 0:
        return None
    return [l.strip() for l in out.splitlines() if l.strip()]


def check_gateway(ctx):
    names = running_distros(ctx)
    if names is None:
        return [], ["cannot list running distros (wsl.exe -l failed), gateway not checked"]
    if GATEWAY not in names:
        return [], []  # never start the gateway, never touch it when it is down
    rc, out = ctx.run([ctx.wsl, "-d", GATEWAY, "-u", "root", "--", "sh", "-c", GATEWAY_SCRIPT], 60)
    changes = [l[8:].strip() for l in out.splitlines() if l.startswith("CHANGED ")]
    problems = [l[8:].strip() for l in out.splitlines() if l.startswith("PROBLEM ")]
    ctx.mask_proven = rc == 0 and not problems and any(l.strip() == "MASKED" for l in out.splitlines())
    if rc != 0 and not problems:
        problems.append("gateway check did not run (exit %d)" % rc)
    return ["gateway: " + c for c in changes], ["gateway: " + p for p in problems]


def check_sysctl(ctx):
    read = lambda p: open(ctx.proc + p).read()
    drift = w.sysctl_drift(read)
    changes, problems = [], []
    for key in sorted(drift):
        try:
            with open(ctx.proc + w.sysctl_path(key), "w") as f:
                f.write("%d" % w.DESIRED[key])
            changes.append("memory reserve %s set to %d (was %s)" % (key, w.DESIRED[key], drift[key]))
        except OSError as e:
            problems.append("memory reserve %s could not be set (%s)" % (key, e.strerror))
    return changes, problems


def gateway_container_running(ctx):
    rc, out = ctx.run([ctx.docker, "ps", "--filter", "name=" + GATEWAY_CONTAINER, "--format", "{{.Names}}"], 30)
    return rc == 0 and GATEWAY_CONTAINER in out.split()


def check_tasks(ctx):
    changes, problems = [], []
    safe = None  # worked out once, and only when a guarded task is actually found Disabled
    for task in KEEPER_TASKS:
        rc, out = ctx.run([ctx.schtasks, "/query", "/tn", task, "/fo", "list", "/v"], 30)
        if rc != 0:
            continue  # this machine has no such task
        m = re.search(r"Scheduled Task State:\s*(\w+)", out)
        if not m or m.group(1).lower() != "disabled":
            continue
        if task in GATEWAY_GUARDED_TASKS:
            if safe is None:
                safe = ctx.mask_proven or gateway_container_running(ctx)
            if not safe:
                continue  # left Disabled on purpose: enabling it could boot the gateway distro with the mask unproven
        rc, out = ctx.run([ctx.schtasks, "/change", "/tn", task, "/enable"], 30)
        if rc == 0:
            changes.append("task %s was Disabled: enabled it" % task)
        else:
            first = (out.strip().splitlines() or ["no output"])[0][:120]
            problems.append("task %s is Disabled and enabling it was refused (%s); run: schtasks.exe /change /tn '%s' /enable" % (task, first, task))
    return changes, problems


def _read_int(path, default=0):
    try:
        with as_owner(), open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return default


def check_docker(ctx):
    changes, problems = [], []
    failf = os.path.join(ctx.state, "wsl-root-check.docker-fails")
    stamp = os.path.join(ctx.state, "wsl-root-check.docker-restart")
    rc, _ = ctx.run(["timeout", "20", ctx.docker, "version", "--format", "{{.Server.Version}}"], 30)
    if rc == 0:
        _remove(failf)
        return changes, problems
    rc, out = ctx.run([ctx.tasklist, "/FI", "IMAGENAME eq Docker Desktop.exe", "/NH"], 20)
    if "Docker Desktop.exe" not in out:
        _remove(failf)  # Docker Desktop is not running: not ours to start
        return changes, problems
    fails = _read_int(failf) + 1
    _write(failf, str(fails))
    if fails < DOCKER_FAILS_NEEDED:
        return changes, problems  # may still be starting; look again next run
    last = _read_int(stamp, 0)
    if ctx.now() - last < DOCKER_RESTART_GAP_S:
        problems.append("docker does not answer but Docker Desktop was restarted within the hour: not restarting again")
        return changes, problems
    rc, out = ctx.run([ctx.docker_win, "ps", "-q"], 30)
    if rc != 0:
        problems.append("docker does not answer and the containers could not be counted: Docker Desktop not restarted")
        return changes, problems
    running = [l for l in out.splitlines() if l.strip()]
    if running:
        problems.append("docker does not answer but %d container(s) run: Docker Desktop not restarted" % len(running))
        return changes, problems
    rc, out = ctx.run([ctx.docker_win, "desktop", "restart"], 300)
    _write(stamp, str(int(ctx.now())))
    _remove(failf)
    if rc == 0:
        changes.append("docker did not answer on %d checks in a row, no container ran: restarted Docker Desktop" % fails)
    else:
        problems.append("docker desktop restart failed (exit %d)" % rc)
    return changes, problems


def _remove(path):
    try:
        with as_owner():
            os.remove(path)
    except OSError:
        pass


_OWNER = None  # (uid, gid) the state folder belongs to; set by main() when running as root


@contextlib.contextmanager
def as_owner():
    """While inside, file work in the state folder runs with the folder owner's identity, so root never follows a planted link."""
    if _OWNER is None or os.geteuid() != 0:
        yield
        return
    uid, gid = _OWNER
    os.setegid(gid)
    os.seteuid(uid)
    try:
        yield
    finally:
        os.seteuid(0)
        os.setegid(0)


def _write(path, text):
    with as_owner(), open(path, "w") as f:
        f.write(text)


def find_state():
    """The state folder of the person this machine belongs to: /home/*/.claude/state, a real folder owned by someone other than root."""
    import glob
    found = []
    for d in sorted(glob.glob("/home/*/.claude/state")):
        try:
            st = os.lstat(d)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and st.st_uid != 0:
            found.append(d)
    with_rec = [d for d in found if os.path.isdir(os.path.join(d, "recovery"))]
    return (with_rec or found or [None])[0]


def record(ctx, changes, problems):
    with as_owner():
        return _record(ctx, changes, problems)


def _record(ctx, changes, problems):
    t = ctx.now()
    os.makedirs(ctx.state, exist_ok=True)
    last = os.path.join(ctx.state, "wsl-root-check.last")
    _write(last, "%s | %d change(s), %d problem(s)\n" % (clock12(t), len(changes), len(problems)))
    if not changes and not problems:
        return None
    lines = ["%s | changed: %s" % (clock12(t), c) for c in changes] + ["%s | PROBLEM: %s" % (clock12(t), p) for p in problems]
    log = os.path.join(ctx.state, "wsl-root-check.log")
    with open(log, "a") as f:
        f.write("%s\n" % datetime.datetime.fromtimestamp(t).strftime("%F"))
        f.write("\n".join(lines) + "\n")
    rec_dir = os.path.join(ctx.state, "recovery")
    os.makedirs(rec_dir, exist_ok=True)
    recent = sorted(f for f in os.listdir(rec_dir) if re.match(r"^\d{4}-\d\d-\d\d-\d{4,6}\.md$", f))
    target = None
    if recent and t - os.path.getmtime(os.path.join(rec_dir, recent[-1])) < 3600:
        target = os.path.join(rec_dir, recent[-1])
    body = "\n## Root check (wsl_root_check.py), %s\n" % clock12(t) + "".join("- %s\n" % l for l in lines)
    if target:
        with open(target, "a") as f:
            f.write(body)
    else:
        target = os.path.join(rec_dir, datetime.datetime.fromtimestamp(t).strftime("%F-%H%M") + ".md")
        with open(target, "a") as f:
            f.write("# Root check, %s\n%s" % (datetime.datetime.fromtimestamp(t).strftime("%a %-d %b %-I:%M %p"), body))
    return target


def run_all(ctx):
    changes, problems = [], []
    for name, fn in (("interop", check_interop), ("gateway", check_gateway), ("sysctl", check_sysctl), ("tasks", check_tasks), ("docker", check_docker)):
        if name in ("gateway", "tasks", "docker") and any("WSLInterop" in p or "binfmt_misc" in p for p in problems):
            problems.append("%s check skipped: Windows programs cannot start while interop is broken" % name)
            continue
        try:
            c, p = fn(ctx)
        except Exception as e:  # one broken check never stops the others
            c, p = [], ["%s check crashed: %s" % (name, e)]
        changes += c
        problems += p
    return changes, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Root-side WSL repair (see the file header)")
    ap.parse_args(argv)
    global _OWNER
    if os.geteuid() != 0 and not os.environ.get("ROOTCHECK_STATE"):
        print("wsl_root_check: must run as root (wsl.exe -d Debian -u root -- ...)", file=sys.stderr)
        return 2
    state = os.environ.get("ROOTCHECK_STATE") or find_state()
    if not state:
        print("wsl_root_check: no state folder found under /home/*/.claude/state owned by a person", file=sys.stderr)
        return 2
    if os.geteuid() == 0:
        st = os.lstat(state)
        if st.st_uid == 0:
            print("wsl_root_check: the state folder belongs to root; refusing", file=sys.stderr)
            return 2
        _OWNER = (st.st_uid, st.st_gid)
    with as_owner():
        os.makedirs(state, exist_ok=True)
        lock = open(os.path.join(state, "wsl-root-check.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another run is working
    ctx = Ctx(state,
              binfmt=os.environ.get("ROOTCHECK_BINFMT", "/proc/sys/fs/binfmt_misc"),
              wsl=os.environ.get("ROOTCHECK_WSL", "wsl.exe"),
              schtasks=os.environ.get("ROOTCHECK_SCHTASKS", "schtasks.exe"),
              tasklist=os.environ.get("ROOTCHECK_TASKLIST", "tasklist.exe"),
              docker=os.environ.get("ROOTCHECK_DOCKER", "docker"),
              docker_win=os.environ.get("ROOTCHECK_DOCKER_WIN", "docker.exe"))
    changes, problems = run_all(ctx)
    record(ctx, changes, problems)
    for c in changes:
        print("changed: " + c)
    for p in problems:
        print("PROBLEM: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
