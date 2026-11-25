#!/usr/bin/env python3
"""Smoke test of an installed no-AI wall, run by install.sh (never installed itself: this file stays in the repo).

Usage: python3 -I smoke.py <claude-folder> <home> <refusal-log>

It runs the hook exactly as settings.json registers it (the command string is read from the settings file and run through bash, so a
wrong path or a missing `python3 -I` fails here) and feeds it tool calls as JSON, the way Claude Code does. Nothing is written or run on the
calls' behalf: the hook only judges them.

  refused:  one probe path per entry of the private list (built from the entry, never printed), plus changes to the wall's own files and
            to the settings file, which the hook must deny with exit 2; plus the bash helper that lane.sh sources.
  ordinary: calls the hook must allow with exit 0: reading a wall file, running the module's status, editing tooling in scripts and hooks,
            a subagent prompt that names the wall's files.

Prints `refused <n>/<n> ordinary <n>/<n>` and exits 0 when every probe came out right, else prints what went wrong on stderr (list entries by
number, never by text) and exits 1. A missing list means the wall is off; the probes then use a stand-in list so the hook itself is still
proved, and one line on stderr says so.
"""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NoReturn


def fail(msg) -> NoReturn:
    sys.stderr.write("smoke: %s\n" % msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 4:
        fail("usage: smoke.py <claude-folder> <home> <refusal-log>")
    live, home, log = sys.argv[1:4]
    wall = os.path.join(live, "no-ai-wall")
    settings = os.path.join(live, "settings.json")

    # The hook command exactly as the settings file registers it.
    try:
        with open(settings, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        fail("the settings file cannot be read (%s)" % type(exc).__name__)
    cmds = [h.get("command", "") for g in (cfg.get("hooks") or {}).get("PreToolUse", []) for h in g.get("hooks", []) if "no-ai-wall-guard.py" in h.get("command", "")]
    if not cmds:
        fail("settings has no PreToolUse entry for the wall's hook")
    for c in cmds:
        if not re.search(r"(^|[\s'\"])python3 -I \S*/\.claude/no-ai-wall/no-ai-wall-guard\.py", c):
            fail("a hook entry does not run `python3 -I` on the hook in the wall's own folder")
    cmd = cmds[0]

    real_list = os.environ.get("NO_AI_PATHS_FILE") or os.path.join(home, ".config", "afa", "no-ai-paths.txt")
    scratch = tempfile.mkdtemp(prefix="wall-smoke.")
    atexit.register(shutil.rmtree, scratch, True)
    standin = False
    if os.path.lexists(real_list):
        listfile = real_list
    else:
        standin = True
        listfile = os.path.join(scratch, "standin-list.txt")
        with open(listfile, "w") as fh:
            fh.write(os.path.join(scratch, "standin-client") + "\n")
        sys.stderr.write("smoke: no no-AI list on this machine, so the wall is off here; probing with a stand-in list\n")
    env = dict(os.environ, HOME=home, NO_AI_PATHS_FILE=listfile, NO_AI_REFUSAL_LOG=log)
    env.pop("PYTHONPATH", None)

    def hook(tool, cwd=None, **ti):
        payload = json.dumps({"tool_name": tool, "tool_input": ti, "cwd": cwd or live})
        r = subprocess.run(["bash", "-c", cmd], input=payload, capture_output=True, text=True, env=env, timeout=30)
        return r.returncode, r.stderr

    problems = []
    refused_ok = refused_n = ordinary_ok = ordinary_n = 0

    # One probe per list entry.
    entries = []
    try:
        with open(listfile, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    entries.append(line)
    except OSError:
        fail("the no-AI list exists but cannot be read")
    helper = os.path.join(wall, "no_ai_wall.sh")
    first_probe = None
    for n, raw in enumerate(entries, 1):
        base = os.path.join(home, raw[2:]) if raw.startswith("~/") else raw
        base = re.sub(r"\[[^\]]*\]", "x", base).replace("*", "x").replace("?", "x").rstrip("/")
        probe = base + "/probe.txt"
        first_probe = first_probe or probe
        refused_n += 1
        rc, err = hook("Read", file_path=probe)
        if rc == 2 and "no-ai wall" in err:
            refused_ok += 1
        else:
            problems.append("a read under list entry %d was not refused (exit %d)" % (n, rc))
    if first_probe:  # the bash helper lane.sh sources must agree with the hook
        refused_n += 1
        r = subprocess.run(["bash", "-c", '. "$1"; no_ai_wall_paths_hit "$2"', "_", helper, first_probe], capture_output=True, text=True, env=env, timeout=30)
        if r.returncode == 0:
            refused_ok += 1
        else:
            problems.append("the bash helper did not refuse a listed path (exit %d)" % r.returncode)

    # Changes to the wall's own files, the settings file and the list itself are denied, even with nothing listed.
    for label, tool, ti in (
        ("an edit of the module", "Edit", dict(file_path=os.path.join(wall, "no_ai_wall.py"))),
        ("a new file in the wall folder", "Write", dict(file_path=os.path.join(wall, "probe-new.txt"))),
        ("an edit of the settings file", "Edit", dict(file_path=settings)),
        ("a shell write into the wall folder", "Bash", dict(command="echo x > %s" % os.path.join(wall, "probe.sh"))),
        ("an edit of the list", "Edit", dict(file_path=listfile)),
        ("an edit of the cleanup command", "Edit", dict(file_path=os.path.join(wall, "wall_cleanup.py"))),
        ("a write of the cleanup command", "Write", dict(file_path=os.path.join(wall, "wall_cleanup.py"))),
        ("a shell removal of the cleanup command", "Bash", dict(command="rm %s" % os.path.join(wall, "wall_cleanup.py"))),
        # Python may run a wall file as itself only: any option that makes it run something else, with the file as a mere argument, is refused.
        ("Python told to run a module, with the wall's module as its argument", "Bash", dict(command="python3 -msomemodule %s" % os.path.join(wall, "no_ai_wall.py"))),
        ("Python told to run a module, with the cleanup command as its argument", "Bash", dict(command="python3 -I -msomemodule %s" % os.path.join(wall, "wall_cleanup.py"))),
        ("Python told to run code from the command line, with the wall's module as its argument", "Bash", dict(command="python3 -cpass %s" % os.path.join(wall, "no_ai_wall.py"))),
        ("the cleanup command handed a path", "Bash", dict(command="python3 -I %s /tmp/somewhere" % os.path.join(wall, "wall_cleanup.py"))),
        ("a variable set in front of Python, its value ending in a trusted program's name", "Bash", dict(command="A=/ls python3 -m somemodule %s" % os.path.join(wall, "no_ai_wall.py"))),
        ("a trusted program's name given a new body in parentheses, then used", "Bash", dict(command="ls() ( python3 -m somemodule %s ); ls" % os.path.join(wall, "no_ai_wall.py"))),
    ):
        refused_n += 1
        rc, err = hook(tool, **ti)
        if rc == 2 and "no-ai wall" in err:
            refused_ok += 1
        else:
            problems.append("%s was not refused (exit %d)" % (label, rc))

    # Ordinary work is not in the way.
    work = os.path.join(scratch, "work")
    os.makedirs(work, exist_ok=True)
    plain = os.path.join(work, "a.txt")
    with open(plain, "w") as fh:
        fh.write("x")
    for label, tool, ti in (
        ("a plain read", "Read", dict(file_path=plain)),
        ("a read of the module", "Read", dict(file_path=os.path.join(wall, "no_ai_wall.py"))),
        ("the module's own status command", "Bash", dict(command="python3 -I %s status" % os.path.join(wall, "no_ai_wall.py"))),
        ("an edit of a script in the scripts folder", "Edit", dict(file_path=os.path.join(live, "scripts", "lane.sh"))),
        ("a new file in the hooks folder", "Write", dict(file_path=os.path.join(live, "hooks", "probe-hook.py"))),
        ("a command that copies into the scripts folder", "Bash", dict(command="cp /tmp/a %s/probe.sh" % os.path.join(live, "scripts"))),
        ("a subagent prompt that names the wall's files", "Agent", dict(prompt="Read %s and %s and report their scope. Do not edit anything." % (os.path.join(wall, "no_ai_wall.py"), settings))),
        ("an ordinary shell command", "Bash", dict(command="ls /tmp")),
        ("the cleanup command, dry run", "Bash", dict(command="python3 -I %s" % os.path.join(wall, "wall_cleanup.py"))),
        ("the cleanup command with --apply", "Bash", dict(command="python3 -I %s --apply" % os.path.join(wall, "wall_cleanup.py"))),
    ):
        ordinary_n += 1
        rc, err = hook(tool, cwd=work, **ti)
        if rc == 0:
            ordinary_ok += 1
        else:
            m = re.search(r"\(entry (\d+)\)", err)
            problems.append("%s was refused (exit %d%s)" % (label, rc, ", list entry %s" % m.group(1) if m else ""))

    if problems:
        for p in problems:
            sys.stderr.write("smoke: %s\n" % p)
        print("refused %d/%d ordinary %d/%d" % (refused_ok, refused_n, ordinary_ok, ordinary_n))
        return 1
    print("refused %d/%d ordinary %d/%d" % (refused_ok, refused_n, ordinary_ok, ordinary_n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
