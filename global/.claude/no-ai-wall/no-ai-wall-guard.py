#!/usr/bin/env python3
"""Claude Code PreToolUse hook: the no-AI wall, tier 1 (member personal data). Denies a tool call that names a path under an
entry of ~/.config/afa/no-ai-paths.txt: Read, Edit, Write, MultiEdit, NotebookEdit, Grep, Glob and Bash by the paths they name (or any
path-looking word in a Bash command), an MCP or unknown tool by any string in its arguments, and EVERY tool when the session's working
folder is listed. A Grep, Glob or recursive shell search whose root CONTAINS a listed folder is denied too, and so is a Grep or Glob
rooted on the list file or a folder above it. Relative paths in any tool's arguments are joined with the session folder first.
The list, everything in ~/.claude/no-ai-wall (this hook, no_ai_wall.py, no_ai_wall.sh: a folder that holds only the wall's own files)
and the settings*.json files cannot be written from a session (reading and running them stays open), with the list full or empty.
Other files in ~/.claude/scripts and ~/.claude/hooks are ordinary tooling and are not guarded. Nothing else is blocked.

stdin: {"tool_name": ..., "tool_input": {...}, "cwd": ...}
deny: reason on stderr (it names the list entry by number, never by path), exit 2, one line in ~/.claude/state/no-ai-refusals.log
(time, tool, entry number; never content).
allow: exit 0. A missing list means the wall is off; a list that exists but cannot be read denies (fail closed).
Fast by design: it runs on every tool call, so no network, no model, nothing but one small file and a few stat calls.
The decisions live in no_ai_wall.py (this folder), shared with lane.sh through no_ai_wall.sh.
"""
import os
import sys

# Nothing but `os` and `sys` (both already loaded by Python itself) is imported before this point. A hook that imported anything else
# from a search path holding a folder a session can write (the working folder, ~/.claude/scripts, PYTHONPATH, the user's site
# folder) would run a planted json.py or re.py inside the wall. Isolated mode (-I) drops the script folder, the working folder,
# PYTHONPATH and the user site from the search path; the standard library is all that is left. Register the hook as
# `python3 -I <this file>` as well, so the first start-up is isolated too.
if not sys.flags.isolated:
    try:
        os.execv(sys.executable, [sys.executable, "-I", os.path.realpath(__file__)] + sys.argv[1:])
    except (OSError, ValueError):  # cannot re-start: keep going, with the search path cut down to the standard library
        _here = os.path.dirname(os.path.realpath(__file__))
        sys.path[:] = [p for p in sys.path if p and os.path.realpath(p) != _here and "site-packages" not in p and "dist-packages" not in p]

import json  # noqa: E402  (standard library, imported only after the search path is safe)
import types  # noqa: E402

HERE = os.path.dirname(os.path.realpath(__file__))


def load_wall():
    """The shared module, compiled from its source at an absolute path in this hook's own folder (~/.claude/no-ai-wall, which holds only the
    wall's files and which a session may not write): never found by a search of sys.path, and never looked for anywhere else, so a
    look-alike in ~/.claude/scripts or any other folder cannot stand in for it."""
    path = os.path.join(HERE, "no_ai_wall.py")
    if not os.path.isfile(path):
        raise ImportError("no_ai_wall.py not found beside the hook")
    # Compile the source itself: a cached .pyc planted in __pycache__ is never read, and none is written.
    sys.dont_write_bytecode = True
    with open(path, "rb") as fh:
        code = compile(fh.read(), path, "exec")
    mod = types.ModuleType("no_ai_wall")
    mod.__file__ = path
    exec(code, mod.__dict__)
    return mod


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    try:
        w = load_wall()
    except Exception:  # the shared module is gone: stay closed only when a list exists to protect
        home = os.environ.get("HOME") or os.path.expanduser("~")
        lists = [os.environ.get("NO_AI_PATHS_FILE") or os.path.join(home, ".config", "afa", "no-ai-paths.txt")]
        if any(os.path.lexists(p) for p in lists):
            sys.stderr.write("no-ai wall: no_ai_wall.py cannot be loaded, so file and shell tools are refused until it is restored\n")
            return 2
        return 0
    try:
        verdict = w.evaluate(payload)
    except Exception as exc:  # a bug in the check must not open the wall
        verdict = (payload.get("tool_name") or "?", "(check failed)", "no-ai wall: the check failed (%s), so this call is refused" % type(exc).__name__)
    if not verdict:
        return 0
    tool, entry, message = verdict
    w.log_refusal(tool, entry)
    sys.stderr.write(message + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
