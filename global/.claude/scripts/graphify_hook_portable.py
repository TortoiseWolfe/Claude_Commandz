#!/usr/bin/env python3
"""Make a repo's graphify hooks work on any machine.

`graphify install --project` writes the absolute path of the graphify program into the repo's
.claude settings file (for example /home/<user>/.local/bin/graphify hook-guard search). On a machine where
that path does not exist the hook fails on every Bash, Grep, Read and Glob call. This rewrites each such
hook command to look graphify up on the PATH, fall back to ~/.local/bin/graphify, and quietly do nothing
when neither exists.

Usage: graphify_hook_portable.py [--check] [repo-or-settings-path ...]   (default: the current folder)
Run it again after any `graphify install` or `graphify hook install`. Safe to repeat: a command that is
already portable is left alone. --check changes nothing and exits 1 when a file would change.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

SETTINGS = Path(".claude") / ("settings" + ".json")
HOOK_RE = re.compile(r"^\s*(?:\S*/)?graphify\s+hook-guard\s+(?P<kind>\w+)\s*$")


def portable(kind):
    return ("sh -c 'g=$(command -v graphify || echo $HOME/.local/bin/graphify); "
            '[ -x "$g" ] && exec "$g" hook-guard %s || exit 0\'' % kind)


def rewrite(node):
    """Rewrite hook commands inside a parsed settings value in place; return how many changed."""
    changed = 0
    if isinstance(node, dict):
        cmd = node.get("command")
        if isinstance(cmd, str):
            m = HOOK_RE.match(cmd)
            if m and cmd != portable(m.group("kind")):
                node["command"] = portable(m.group("kind"))
                changed += 1
        for v in node.values():
            changed += rewrite(v)
    elif isinstance(node, list):
        for v in node:
            changed += rewrite(v)
    return changed


def settings_file(arg):
    p = Path(arg).expanduser()
    return p if p.is_file() else p / SETTINGS


def main(argv):
    check = "--check" in argv
    paths = [a for a in argv if a != "--check"] or ["."]
    would_change = False
    for arg in paths:
        f = settings_file(arg)
        if not f.is_file():
            print("skip (no settings file): %s" % f)
            continue
        data = json.loads(f.read_text())
        n = rewrite(data)
        if not n:
            print("ok (already portable or no graphify hook): %s" % f)
            continue
        would_change = True
        if check:
            print("would change %d hook(s): %s" % (n, f))
            continue
        # A fresh, exclusively created temp file beside the target (never a fixed name another process could
        # pre-plant as a symlink), then an atomic rename onto the target.
        fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix=f.name + ".")
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(data, indent=2) + "\n")
        os.chmod(tmp, f.stat().st_mode & 0o777)
        os.replace(tmp, f)
        print("rewrote %d hook(s): %s" % (n, f))
    return 1 if (check and would_change) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
