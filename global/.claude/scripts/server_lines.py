#!/usr/bin/env python3
"""server_lines.py: run one command on a server and return its answer as clean lines.

Use when: a lane must read what a server tool prints (an occ call, a read-only wrapper, a remote status command) and match or list its lines.
Why it exists: a server tool's lines can end in a carriage return (CR, the invisible Windows line-end byte), so a pattern that ends
in ":$" matches nothing; one lane wrote the same fix script three times for that (Claude_Commandz issue 30). This helper removes the CR once.

  server_lines.py ro    [--grep REGEX] [--json] [--timeout S] -- COMMAND [ARG...]
  server_lines.py write --reason TEXT  [--json] [--timeout S] -- COMMAND [ARG...]

  ro     the command's file name must end in -ro, -ro.sh or -ro.py (the read-only wrappers); anything else is refused with exit 3.
  write  the one separately named way to run a command that changes a server; it needs --reason and writes a line to stderr first.
         A session's own safety check still decides whether the call is allowed; this command does not get around it.

Output: one line per line of the command's answer (stdout and stderr together, CR removed, final empty line dropped). With --json:
{"rc": N, "lines": [...]}. --grep keeps only the matching lines. The exit code is the command's own (124 on a timeout).
No shell is used: the command is a list of words, so nothing in an argument is expanded.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REFUSED = 3


def clean_lines(text):
    """-> the lines of text with every carriage return removed and the closing empty line dropped."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def is_read_only_name(program):
    base = os.path.basename(program)
    return bool(re.search(r"-ro(\.sh|\.py)?$", base))


def run(argv, timeout=60):
    """-> (exit code, lines). Stdout and stderr are read together; 124 when the command runs past the timeout."""
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return 124, clean_lines((exc.stdout or b"").decode("utf-8", "replace"))
    except FileNotFoundError:
        return 127, [f"server_lines: command not found: {argv[0]}"]
    return done.returncode, clean_lines(done.stdout.decode("utf-8", "replace"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="server_lines.py", description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)
    for name in ("ro", "write"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")
        s.add_argument("--timeout", type=float, default=60)
        if name == "ro":
            s.add_argument("--grep")
        else:
            s.add_argument("--reason", required=True)
        s.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("server_lines: no command after --", file=sys.stderr)
        return 2
    if args.mode == "ro" and not is_read_only_name(command[0]):
        print(f"server_lines: ro runs read-only wrappers only (name ends in -ro, -ro.sh or -ro.py), not {os.path.basename(command[0])}; "
              "a command that changes a server goes through the write command", file=sys.stderr)
        return REFUSED
    if args.mode == "write":
        print(f"server_lines: WRITE ({args.reason}): {os.path.basename(command[0])}", file=sys.stderr)
    rc, lines = run(command, args.timeout)
    if args.mode == "ro" and args.grep:
        rx = re.compile(args.grep)
        lines = [ln for ln in lines if rx.search(ln)]
    if args.json:
        print(json.dumps({"rc": rc, "lines": lines}))
    else:
        for ln in lines:
            print(ln)
    return rc


if __name__ == "__main__":
    sys.exit(main())
