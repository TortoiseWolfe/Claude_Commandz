#!/usr/bin/env python3
"""openclaw_gateway.py: the one way every script in this folder reaches OpenClaw's gateway.

OpenClaw runs in Docker (the container openclaw-openclaw-gateway-1, user node) on the tower, and is moving there on NX-01. Until NX-01 has
moved, its gateway is still the locked WSL distro. 5 Nov 2026 9:30 PM every hub post on the tower failed (wsl.exe exit 255, "there is no
distribution with the supplied name", an empty log line) because twelve scripts each named the distro by hand. They now ask this module.

Route (Docker first):
  1. AFA_OC_GATEWAY=docker|wsl forces it (tests always force wsl, see tests/state_guard.py).
  2. the container is running                      -> docker exec -i
  3. else wsl.exe lists the distro                  -> wsl.exe -d <distro>   (REMOVE once NX-01 runs in Docker; kept as the fallback so
     the distro can sit stopped as NX-01's rollback: asking the container first never wakes the distro)
  4. else                                           -> docker (it fails with Docker's own message, which the log now carries)

From Python:   import openclaw_gateway as gw;  subprocess.run(gw.argv(["bash", "-c", script]), input=...)
From a shell:  . "$here/openclaw_gateway.sh";  oc_gateway [-u root] [-t] [--wsl] -- cmd args...      (it runs this file's `exec`)
Probe:         python3 openclaw_gateway.py route | home | workspace

Paths differ by route, so scripts use `$HOME` inside the command and home() / workspace() outside it. workspace() is None on Docker:
the tower's front desk has no workspace that reads the hub's MEMORY.md blocks or OUTBOX.md, so the writers that need one say so and skip
(nothing is created in the tower agent's files until the hub decides it should be).
`distro_only=True` / --wsl is for work that only means something inside the WSL distro (the uid remap, its staged test): it never goes to
the container, where it would run as root.
"""
import os
import subprocess
import sys
import time

GATEWAY_DISTRO = "OpenClawGateway"  # REMOVE with the wsl route once NX-01 runs OpenClaw in Docker
GATEWAY_CONTAINER = "openclaw-openclaw-gateway-1"
ROUTE_ENV = "AFA_OC_GATEWAY"
ROUTE_TTL = 60
_seen = [0.0, None]  # when, route


def _distro_listed():
    try:
        r = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=20, cwd=os.path.expanduser("~"))
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    names = r.stdout.decode("utf-16-le", "replace") if b"\0" in r.stdout else r.stdout.decode("utf-8", "replace")
    return GATEWAY_DISTRO in names.replace("\0", "").split()


def _container_running():
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", GATEWAY_CONTAINER], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def route():
    """-> "docker" or "wsl" (see the header). Kept for ROUTE_TTL seconds inside one process."""
    forced = os.environ.get(ROUTE_ENV, "")
    if forced in ("docker", "wsl"):
        return forced
    now = time.time()
    if _seen[1] and now - _seen[0] < ROUTE_TTL:
        return _seen[1]
    found = "docker" if _container_running() else "wsl" if _distro_listed() else "docker"
    _seen[:] = [now, found]
    return found


def forget():
    _seen[:] = [0.0, None]


def home(distro_only=False):
    """The gateway user's home, as that user's shell sees it."""
    return "/home/openclaw" if distro_only or route() == "wsl" else "/home/node"


def workspace():
    """The front desk's workspace folder (MEMORY.md, OUTBOX.md live there), or None when this machine's gateway has none the hub writes."""
    return home() + "/.openclaw/workspace" if route() == "wsl" else None


def name():
    """How to say where it runs, for a message to a person."""
    return "the OpenClaw gateway container" if route() == "docker" else "the OpenClawGateway distro"


def argv(inner, user="openclaw", tty=False, distro_only=False, via_shell=False):
    """-> the command line that runs `inner` (a list) inside the gateway as user ("openclaw" is node in the container). Stdin passes
    through. tty=True is for a terminal window (docker exec -it). via_shell=True lets wsl.exe's own shell find the program (the tray
    profile's `openclaw tui`); the container needs no such help."""
    inner = list(inner)
    if not distro_only and route() == "docker":
        cmd = ["docker", "exec", "-it" if tty else "-i", "-w", "/home/node"]
        if user == "root":
            cmd += ["-u", "root"]
        return cmd + [GATEWAY_CONTAINER] + inner
    return ["wsl.exe", "-d", GATEWAY_DISTRO, "-u", user, "--" if via_shell else "-e"] + inner


def why_failed(r):
    """One short line on why a call failed: its stderr and stdout (wsl.exe prints its own errors in UTF-16, and some on stdout), NULs
    and carriage returns removed. Never empty: a log line with nothing after stderr= is what hid the 5 Nov failure."""
    if r is None:
        return "no process result"
    out = lambda v: v.decode("utf-8", "replace") if isinstance(v, bytes) else (v or "")
    text = "%s %s" % (out(r.stderr), out(r.stdout))
    text = " ".join(text.replace("\0", "").replace("\r", " ").split())[-300:]
    return text or "(no output)"


def _main(args):
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    if args[0] == "route":
        print(route())
        return 0
    if args[0] == "home":
        print(home())
        return 0
    if args[0] == "workspace":
        w = workspace()
        print(w or "")
        return 0 if w else 1
    if args[0] == "exec":
        rest, user, tty, wsl = args[1:], "openclaw", False, False
        while rest and rest[0] != "--":
            flag = rest.pop(0)
            if flag == "-u" and rest:
                user = rest.pop(0)
            elif flag == "-t":
                tty = True
            elif flag == "--wsl":
                wsl = True
            else:
                print("openclaw_gateway.py exec [-u USER] [-t] [--wsl] -- cmd args...", file=sys.stderr)
                return 2
        if not rest or rest[0] != "--" or len(rest) < 2:
            print("openclaw_gateway.py exec [-u USER] [-t] [--wsl] -- cmd args...", file=sys.stderr)
            return 2
        cmd = argv(rest[1:], user=user, tty=tty, distro_only=wsl)
        os.execvp(cmd[0], cmd)
    print("unknown command: %s" % args[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
