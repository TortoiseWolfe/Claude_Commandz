#!/usr/bin/env python3
"""Claude Code PreToolUse hook: a session may not change the two merge list files.

~/.config/afa/merge-repos.txt lists the repos lane_merge.py may merge in, and
~/.config/afa/own-review-repos.txt lists the repos it must never merge in. A session
runs as the same user, so without this hook it could rewrite either list. A person
edits the lists in a plain terminal.

stdin: {"tool_name": ..., "tool_input": {...}}
block: reason on stderr, exit 2.  allow: exit 0.  The reason never echoes the command.

Write, Edit, MultiEdit and NotebookEdit are refused when the path (spelled any way,
through "..", "~" or a symlink) is one of the two files. Bash is refused when the command
names either file, uses a glob that would match one inside .config/afa, or moves,
removes, links, copies, or changes the .config/afa folder itself. Reading the lists
with the Read tool stays allowed.

Honest limit: this stops direct and careless edits (the tool calls, a command that names
the file, a glob, moving the folder). A command that builds the name from pieces is not
seen. The session's own safety check and lane_merge.py's refusals remain the other layers.

Unreadable or non-JSON stdin: allow, as lane-watch-guard.py does. The fancier Bash checks
(glob, folder) allow on an unexpected error after one line on stderr.
"""
import fnmatch
import json
import os
import re
import sys

PROTECTED = (
    os.path.expanduser("~/.config/afa/merge-repos.txt"),
    os.path.expanduser("~/.config/afa/own-review-repos.txt"),
)
BASE_NAMES = tuple(os.path.basename(p) for p in PROTECTED)
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

FOLDER = re.compile(r"\.config/afa/?(?=$|[\s\"';&|)])")
FOLDER_VERBS = re.compile(
    r"(?<![\w.-])(?:mv|rm|rmdir|ln|cp|rsync|chmod|chown|install|tar)(?![\w.-])"
)
WORD_SPLIT = re.compile(r"[\s\"';&|()<>=,`]+")


def reason_for(name):
    return (
        f"BLOCKED: {name} is one of the merge lists; a session may not change or name it "
        "in a shell command. View it with the Read tool. A person edits it in a plain terminal."
    )


def check_path(tool_input):
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        raw = tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return None
    lexical = os.path.abspath(os.path.expanduser(raw))
    real = os.path.realpath(lexical)
    for protected in PROTECTED:
        if lexical == os.path.abspath(protected) or real == os.path.realpath(protected):
            return os.path.basename(protected)
    return None


def glob_hit(command):
    for word in WORD_SPLIT.split(command):
        if ".config/afa" not in word:
            continue
        last = word.rsplit("/", 1)[-1]
        if not any(c in last for c in "*?["):
            continue
        for name in BASE_NAMES:
            if fnmatch.fnmatch(name, last):
                return name
    return None


def check_bash(command):
    for name in BASE_NAMES:
        if name in command:
            return name
    try:
        hit = glob_hit(command)
        if hit:
            return hit
        if FOLDER.search(command) and FOLDER_VERBS.search(command):
            return ".config/afa folder"
    except Exception as exc:
        print(f"list-file-guard internal error: {type(exc).__name__}", file=sys.stderr)
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    hit = None
    if tool in EDIT_TOOLS:
        hit = check_path(tool_input)
    elif tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            hit = check_bash(command)
    if hit:
        print(reason_for(hit), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
