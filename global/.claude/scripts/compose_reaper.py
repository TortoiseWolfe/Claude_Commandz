#!/usr/bin/env python3
"""Stop compose stacks that belong to a worktree nobody is using, and report big containers that have no memory limit (issue #107, part 2).

Why (24 Oct 2026): a dead lane's Next.js dev server (a compose project in a worktree, no mem_limit) ran 16 hours and grew to 12 GB while the VM
ran out of memory.

  compose_reaper.py              dry run: print what it would stop and what it would flag; change nothing
  compose_reaper.py --apply      run `docker compose -p <project> down` for each stale project (never `-v`: volumes stay), log it, print the report

A compose project is stale when ALL of these hold:
  - its working folder (the com.docker.compose.project.working_dir label) is inside ~/repos/.worktrees (a project anywhere else is never touched);
  - no tmux pane is working inside that folder;
  - nothing in the folder, apart from node_modules, .next, .git and similar, changed in the last IDLE_HOURS hours (default 3).
A running container with no memory limit that uses over 4 GB is only reported, never stopped.

Prints nothing when there is nothing to say; the hub's watcher (or a person) decides when to run it. Exit 0 always.
"""
import argparse
import os
import re
import subprocess
import sys
import time

IDLE_HOURS = 3
BIG_BYTES = 4 * 1024**3
SKIP_DIRS = {"node_modules", ".next", ".git", ".cache", "__pycache__", ".turbo", "dist", "build"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WORKTREES = os.path.expanduser("~/repos/.worktrees")
LOG = os.path.expanduser("~/.claude/state/compose-reaper.log")
UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}


def _run(argv, timeout=30):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def docker_containers():
    """(container id, compose project, working folder) for every running compose container."""
    fmt = '{{.ID}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}'
    out = []
    for line in _run(["timeout", "20", "docker", "ps", "--format", fmt]).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[1]:
            out.append(tuple(parts))
    return out


def tmux_panes():
    return [p for p in _run(["tmux", "list-panes", "-a", "-F", "#{pane_current_path}"], 10).splitlines() if p]


def _bytes(text):
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    return int(float(m.group(1)) * UNITS.get(m.group(2).upper(), 1)) if m else 0


def docker_stats():
    """(container id, name, bytes used, memory limit in bytes; 0 = no limit) for every running container."""
    ids = _run(["timeout", "20", "docker", "ps", "-q"]).split()
    if not ids:
        return []
    limits = {}
    for line in _run(["timeout", "20", "docker", "inspect", "--format", "{{.Id}}\t{{.HostConfig.Memory}}"] + ids).splitlines():
        cid, _, mem = line.partition("\t")
        limits[cid[:12]] = int(mem) if mem.strip().isdigit() else 0
    out = []
    for line in _run(["timeout", "40", "docker", "stats", "--no-stream", "--format", "{{.ID}}\t{{.Name}}\t{{.MemUsage}}"], 45).splitlines():
        cid, name, usage = (line.split("\t") + ["", "", ""])[:3]
        out.append((cid[:12], name, _bytes(usage.split("/")[0]), limits.get(cid[:12], 0)))
    return out


def compose_down(project):
    return bool(subprocess.run(["timeout", "120", "docker", "compose", "-p", project, "down"], capture_output=True, timeout=130).returncode == 0)


def newest_change(folder):
    """The newest file time in the folder, skipping caches and dependency folders; the folder's own time when it is empty."""
    best = os.stat(folder).st_mtime
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            try:
                best = max(best, os.lstat(os.path.join(root, f)).st_mtime)
            except OSError:
                pass
    return best


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def reap(containers=docker_containers, panes=tmux_panes, stats=docker_stats, now=None, worktrees_root=WORKTREES, idle_hours=IDLE_HOURS, apply=False,
         down=compose_down):
    now = time.time() if now is None else now
    result = {"would_reap": [], "reaped": [], "kept": [], "unbounded": []}
    pane_list = panes()
    seen = set()
    for _container, project, folder in containers():
        if project in seen:
            continue
        seen.add(project)
        if not NAME_RE.match(project):
            result["kept"].append((project, "odd project name"))
            continue
        if not folder or not os.path.isdir(folder) or not inside(folder, worktrees_root) or os.path.realpath(folder) == os.path.realpath(worktrees_root):
            result["kept"].append((project, "not in a worktree"))
            continue
        if any(inside(p, folder) for p in pane_list):
            result["kept"].append((project, "a session is working in it"))
            continue
        idle = (now - newest_change(folder)) / 3600
        if idle < idle_hours:
            result["kept"].append((project, "changed %.1f hours ago" % idle))
            continue
        line = "%s (worktree %s, untouched %.0f hours)" % (project, os.path.basename(os.path.realpath(folder)), idle)
        if apply and down(project):
            result["reaped"].append(line)
        else:
            result["would_reap"].append(line)
    for cid, name, used, limit in stats():
        if limit == 0 and used > BIG_BYTES:
            result["unbounded"].append((cid, name, used))
    return result


def report_text(result, apply):
    lines = []
    for x in result["reaped"]:
        lines.append("stopped compose project " + x + "; its volumes were kept")
    for x in result["would_reap"]:
        lines.append(("could not stop " if apply else "would stop ") + "compose project " + x)
    for _container, name, used in result["unbounded"]:
        lines.append("container %s uses %.1f GB and has no memory limit (set mem_limit in its compose file)" % (name, used / 1000**3))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--idle-hours", type=float, default=IDLE_HOURS)
    args = ap.parse_args(argv)
    text = report_text(reap(apply=args.apply, idle_hours=args.idle_hours), args.apply)
    if text:
        print(text)
        if args.apply:
            try:
                os.makedirs(os.path.dirname(LOG), exist_ok=True)
                with open(LOG, "a") as f:
                    f.write("%s\n%s\n" % (time.strftime("%F %T"), text))
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
