#!/usr/bin/env python3
"""Keep the WSL-to-Windows bridge answering by keeping big free memory blocks available.

Why (19 Oct 2026, proven from dmesg and /proc/buddyinfo): every Windows command from WSL (cmd.exe, powershell.exe, wsl.exe,
the OpenClaw bridge) opens a Hyper-V socket channel, and hvs_probe -> vmbus_open -> vmbus_alloc_ring needs ONE contiguous 512 KB
(order-7) kernel allocation. After ~33 h the VM had 896 MB free, 32 GB of page cache and no free block at order 7 or above, so
every new channel failed ("page allocation failure: order:7" in kworker hv_pri_chan), Windows waited on accept() and the bridge went
quiet. Dropping clean cache and then compacting gave 2438 free order-7+ blocks and cmd.exe answered in 0.05 s again. The WSL kernel
also ships with vm.compaction_proactiveness=0 and vm.watermark_boost_factor=0, so nothing defragments in the background.

  wsl_bridge_watchdog.py              check; heal when free order-7+ blocks are below the threshold; re-apply the sysctl reserve if it drifted
  wsl_bridge_watchdog.py --probe      also run the 10 s `cmd.exe /c echo ok` probe when nothing needed healing (the 15-minute task)
  wsl_bridge_watchdog.py --dry-run    report what it would do, change nothing
  wsl_bridge_watchdog.py --print-sysctl-conf   the text of /etc/sysctl.d/99-wsl-bridge.conf (this file is the one source of the values)

Heal = (compact if 2 GiB or more is free) then (drop clean page cache, then compact). It needs root and has no sudo: it goes through a
privileged alpine container (one kernel for every distro and Docker; does not depend on the bridge), then `wsl.exe -u root`.
A heal that does not bring the blocks back, or a bridge that still fails the probe after one, writes the alert file that lane_watch.sh
turns into a wake of the hub. Exit 0 healthy or healed, 2 not.
"""
import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
from datetime import datetime

ORDER = 7  # 2**7 pages = 512 KB, the vmbus ring allocation
MIN_BLOCKS = int(os.environ.get("WSL_BRIDGE_MIN_BLOCKS", "200"))  # order-7 equivalents (100 MB). Healthy ~3500, failing 0.
COMPACT_ONLY_MIN_FREE_KB = 2 * 1024 * 1024
HEAL_GAP_S = 300  # never heal twice inside five minutes
ALERT_GAP_S = 1800  # a standing failure re-wakes the hub at most every half hour
IMAGE = "alpine:3"

# Reserve of free memory the kernel keeps by reclaiming clean cache, so freed pages can merge back into big blocks.
#  min_free_kbytes 1 GiB: 2.5% of the 40 GB VM. WSL returns free pages to Windows (free page reporting), so it costs nothing real.
#  watermark_scale_factor 50: kswapd wakes below min + 0.5% of RAM (~1.2 GB free) and stops at ~1.4 GB; the default 10 left ~50 MB.
#  compaction_proactiveness 30: the kernel default is 20, WSL's build sets 0. Background compaction starts well before order-7 runs out.
DESIRED = {
    "vm.min_free_kbytes": 1048576,
    "vm.watermark_scale_factor": 50,
    "vm.compaction_proactiveness": 30,
}


def state_dir():
    return os.environ.get("WSL_BRIDGE_STATE") or os.path.join(os.path.expanduser("~"), ".claude", "state", "wsl-bridge")


def read_file(path):
    with open(path) as f:
        return f.read()


def parse_buddyinfo(text):
    """{zone: [free block count at order 0, 1, ...]} from /proc/buddyinfo text."""
    zones = {}
    for line in text.splitlines():
        m = re.match(r"\s*Node\s+(\d+),\s+zone\s+(\S+)\s+((?:\d+\s*)+)$", line)
        if m:
            zones["%s/%s" % (m.group(1), m.group(2))] = [int(x) for x in m.group(3).split()]
    return zones


def big_blocks(text, order=ORDER):
    """Free blocks of order `order` or larger across all zones, counted in order-`order` units (an order-9 block is 4)."""
    return sum(c << (k - order) for counts in parse_buddyinfo(text).values() for k, c in enumerate(counts) if k >= order)


def mem_free_kb(meminfo):
    m = re.search(r"^MemFree:\s+(\d+)\s+kB", meminfo, re.M)
    return int(m.group(1)) if m else 0


def sysctl_path(key):
    return "/proc/sys/" + key.replace(".", "/")


def sysctl_drift(read=read_file):
    """{key: current} for each reserve value that is not what DESIRED says (a missing file counts as drift)."""
    drift = {}
    for key, want in DESIRED.items():
        try:
            cur = int(read(sysctl_path(key)).strip())
        except (OSError, ValueError):
            cur = None
        if cur != want:
            drift[key] = cur
    return drift


def sysctl_conf():
    lines = ["# Written by wsl_bridge_watchdog.py --print-sysctl-conf (19 Oct 2026). Keeps big free blocks so Windows commands from WSL can open",
             "# their Hyper-V channel (order-7 allocation). Applied by the [boot] command in /etc/wsl.conf (this distro has no systemd) and",
             "# re-applied by the watchdog if it drifts."]
    lines += ["%s = %d" % kv for kv in DESIRED.items()]
    return "\n".join(lines) + "\n"


def run_root(script, timeout=100):
    """Run a root shell script. Returns (ok, route). Docker first (does not need the bridge), then wsl.exe -u root."""
    routes = [
        ("docker", ["timeout", "90", "docker", "run", "--rm", "--privileged", "--pull=never", "--network", "none", IMAGE, "sh", "-c", script]),
        ("wsl", ["timeout", "40", "wsl.exe", "-d", "Debian", "-u", "root", "-e", "sh", "-c", script]),
    ]
    for name, argv in routes:
        try:
            r = subprocess.run(argv, capture_output=True, timeout=timeout, cwd="/mnt/c" if os.path.isdir("/mnt/c") else "/")
        except (subprocess.TimeoutExpired, OSError):
            continue
        if r.returncode == 0:
            return True, name
    return False, "none"


def probe_bridge(timeout=10):
    """True when a Windows command answers inside `timeout` seconds."""
    try:
        r = subprocess.run(["cmd.exe", "/c", "echo ok"], capture_output=True, timeout=timeout, cwd="/mnt/c" if os.path.isdir("/mnt/c") else "/")
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0 and b"ok" in r.stdout


def stamp(now):
    return datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")


def clock12(now):
    return datetime.fromtimestamp(now).strftime("%-I:%M %p")


def log_line(sd, line):
    os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "heal.log"), "a") as f:
        f.write(line + "\n")


def file_age(path, now):
    try:
        return now - os.stat(path).st_mtime
    except OSError:
        return 1e9


def write_stamped(path, text, now):
    """Write `text` and set the file's time to `now`, so file_age() and the callers' clock agree (the tests run on a fake clock)."""
    with open(path, "w") as f:
        f.write(text)
    os.utime(path, (now, now))


def write_alert(sd, now, text):
    """lane_watch.sh wakes the hub when this file's time changes. A standing failure re-wakes it at most every ALERT_GAP_S."""
    path = os.path.join(sd, "alert")
    if file_age(path, now) < ALERT_GAP_S:
        return
    write_stamped(path, "%s %s\n" % (clock12(now), text), now)


def heal(read, root, buddy_text):
    """Returns (steps, route, buddy_text_after, ok). Re-reads /proc/buddyinfo after each step."""
    steps = []
    route = "none"
    text = buddy_text
    if mem_free_kb(read("/proc/meminfo")) >= COMPACT_ONLY_MIN_FREE_KB:
        ok, route = root("echo 1 > /proc/sys/vm/compact_memory")
        steps.append("compact" if ok else "compact-failed")
        text = read("/proc/buddyinfo")
        if big_blocks(text) >= MIN_BLOCKS:
            return steps, route, text, True
    ok, route2 = root("echo 1 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory")
    steps.append("drop_caches+compact" if ok else "drop_caches+compact-failed")
    text = read("/proc/buddyinfo")
    return steps, route2 if ok else route, text, big_blocks(text) >= MIN_BLOCKS


def heal_log_tail(sd):
    try:
        with open(os.path.join(sd, "heal.log")) as f:
            return f.read()[-600:].rstrip("\n").rsplit("\n", 1)[-1]
    except OSError:
        return ""


def snapshot_io():
    """{pid: read_bytes} for every process this user can read /proc/<pid>/io of."""
    snap = {}
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open("/proc/%s/io" % p) as f:
                    snap[int(p)] = int(re.search(r"^read_bytes:\s+(\d+)", f.read(), re.M).group(1))
            except (OSError, AttributeError, ValueError):
                pass
    return snap


def busiest_reader(a, b, secs):
    """(pid, MB/s) of the process that read the most between two snapshots, or None when nothing read over 20 MB/s."""
    rates = sorted(((b[p] - a[p]) / secs / 1048576, p) for p in b if p in a)
    return (rates[-1][1], rates[-1][0]) if rates and rates[-1][0] >= 20 else None


def proc_cmd(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        return ""


def lane_of(pid):
    """The lanes tmux window whose pane is an ancestor of `pid`, else ''. Used only to name who started a heavy reader."""
    try:
        panes = subprocess.run(["tmux", "list-windows", "-t", "lanes", "-F", "#{pane_pid} #{window_name}"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    names = dict(l.split(" ", 1) for l in panes.splitlines() if " " in l)
    while pid > 1:
        if str(pid) in names:
            return names[str(pid)]
        try:
            with open("/proc/%d/stat" % pid) as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return ""
    return ""


def describe_churn(snapshot=snapshot_io, lane=lane_of, secs=2):
    """Who is filling the page cache right now: 'pid 160023 244MB/s (lane greg-drafts-check) bfs -S dfs / -iname ...', or ''."""
    a = snapshot()
    time.sleep(secs)
    hit = busiest_reader(a, snapshot(), secs)
    if not hit:
        return ""
    pid, rate = hit
    who = lane(pid)
    return "pid %d %dMB/s%s %s" % (pid, rate, " (lane %s)" % who if who else "", proc_cmd(pid)[:110])


def run(argv=None, read=read_file, root=run_root, probe=probe_bridge, now=time.time, churn_of=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--probe", action="store_true", help="run the bridge probe even when nothing needed healing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-sysctl-conf", action="store_true")
    args = ap.parse_args(argv)
    if args.print_sysctl_conf:
        sys.stdout.write(sysctl_conf())
        return 0

    sd = state_dir()
    os.makedirs(sd, exist_ok=True)
    lock = open(os.path.join(sd, "watchdog.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another run is healing; it will log

    t = now()
    buddy = read("/proc/buddyinfo")
    before = big_blocks(buddy)
    free_before = mem_free_kb(read("/proc/meminfo")) // 1024
    notes = []

    drift = sysctl_drift(read)
    if drift:
        if args.dry_run:
            notes.append("sysctl-drift:" + ",".join(sorted(drift)))
        else:
            ok, route = root("; ".join("echo %d > %s" % (v, sysctl_path(k)) for k, v in DESIRED.items()))
            notes.append("sysctl-reapplied(%s)" % route if ok else "sysctl-reapply-failed")
            if not ok:
                write_alert(sd, t, "wsl bridge watchdog: cannot reach root to re-apply the memory reserve (docker and wsl.exe -u root both failed)")

    # A low block count starts a heal, but it is not proof the bridge is down (19 Oct 9:27 PM: 7 blocks at one instant, cmd.exe still
    # answered in 0.05 s): only the probe decides whether the hub is woken. Inside the heal gap the probe decides whether to heal anyway.
    low = before < MIN_BLOCKS
    steps, route, after, probe_ok, root_failed = [], "none", buddy, None, False
    churn = ""
    if low and not args.dry_run:
        churn = (churn_of or describe_churn)()
    if low and args.dry_run:
        notes.append("would-heal")
    elif low:
        gap_ok = file_age(os.path.join(sd, "last-heal"), t) >= HEAL_GAP_S
        if not gap_ok:
            probe_ok = probe()
            notes.append("heal-skipped(<%ds since the last, bridge %s)" % (HEAL_GAP_S, "answers" if probe_ok else "DOWN: healing anyway"))
            gap_ok = not probe_ok
        if gap_ok:
            steps, route, after, _ = heal(read, root, buddy)
            root_failed = any(x.endswith("-failed") for x in steps)
            write_stamped(os.path.join(sd, "last-heal"), stamp(t) + "\n", t)
            probe_ok = probe()
    elif args.probe and not args.dry_run:
        probe_ok = probe()

    now_blocks = big_blocks(after)
    if not args.dry_run and (steps or notes):
        line = "%s order%d+ blocks %d->%d (min %d) steps=%s route=%s free_mb %d->%d probe=%s%s%s" % (
            stamp(t), ORDER, before, now_blocks, MIN_BLOCKS, ",".join(steps) or "none", route, free_before,
            mem_free_kb(read("/proc/meminfo")) // 1024, {True: "ok", False: "FAIL", None: "skipped"}[probe_ok],
            (" " + " ".join(notes)) if notes else "", (" churn=" + churn) if churn else "")
        # the 15-minute task and lane_watch.sh can both skip in the same second: one line per state is enough
        if steps or not heal_log_tail(sd).endswith(line[len(stamp(t)):]):
            log_line(sd, line)
            print(line)
    elif args.dry_run and (low or notes):
        print("dry-run: order%d+ blocks %d (min %d) %s" % (ORDER, before, MIN_BLOCKS, " ".join(notes)))

    if probe_ok is False or root_failed:
        write_alert(sd, t, "wsl bridge: Windows commands from WSL %s; free order-%d+ blocks %d (min %d)%s. See %s"
                    % ("are NOT answering (10 s probe failed)" if probe_ok is False else "could not be healed (no root route)",
                       ORDER, now_blocks, MIN_BLOCKS, (", page-cache churn: " + churn) if churn else "", os.path.join(sd, "heal.log")))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
