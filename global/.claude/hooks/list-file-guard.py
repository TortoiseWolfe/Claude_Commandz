#!/usr/bin/env python3
"""Claude Code PreToolUse hook: a session may not change the two merge list files.

~/.config/afa/merge-repos.txt lists the repos lane_merge.py may merge in, and
~/.config/afa/own-review-repos.txt lists the repos it must never merge in. A session
runs as the same user, so without this hook it could rewrite either list. A person
edits the lists in a plain terminal.

stdin: {"tool_name": ..., "tool_input": {...}, "cwd": ...}
block: reason on stderr, exit 2.  allow: exit 0.  The reason never echoes the command.

Write, Edit, MultiEdit and NotebookEdit are refused when the path (spelled any way,
through "..", "~" or a symlink) is one of the two list files or the installed copy of
this hook. Reading any of them with the Read tool stays allowed.

Bash is refused when the command:
  - contains either list file's name, anywhere;
  - has a word that resolves to a list file or to the installed copy of this hook. A word
    is resolved against the session's folder and against every folder the command changes
    into (cd, pushd), with ~, $HOME, ".." and symlinks followed;
  - has a wildcard or brace word that could match a list file, this hook, or (with a verb
    below) one of the guarded folders, resolved the same way;
  - names the .config/afa folder as a whole together with a verb that moves, removes,
    links, copies, walks or re-permissions (mv, rm, cp, find, chmod and the like);
  - names ~/.config or ~/.claude/hooks as a whole together with a verb that moves, removes,
    links or re-permissions it.

Honest limit: this is a tripwire for direct and careless edits, not a wall. A command that
builds the path from pieces, reaches it through a variable other than HOME, or runs a script
file that does the edit is not seen, and a session that can edit the settings file can
remove this hook. The sturdy fix is ownership: lists a session's user cannot write. The
session's own safety check and lane_merge.py's refusals remain the other layers.

Unreadable or non-JSON stdin: allow, as lane-watch-guard.py does. An unexpected error in the
path checks allows after one line on stderr, so a fault here cannot stop every shell call;
the plain name check runs first and has nothing in it that can fail.
"""
import fnmatch
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
AFA = os.path.join(HOME, ".config", "afa")
CONFIG = os.path.join(HOME, ".config")
HOOKS = os.path.join(HOME, ".claude", "hooks")
PROTECTED = (
    os.path.join(AFA, "merge-repos.txt"),
    os.path.join(AFA, "own-review-repos.txt"),
)
SELF = os.path.join(HOOKS, "list-file-guard.py")
BASE_NAMES = tuple(os.path.basename(p) for p in PROTECTED)
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MAX_FOLDERS = 24

# Verbs that act on a folder as a whole. The list folder gets the long list; its parent and
# the hooks folder get the short one, because copying into or searching them is everyday work.
AFA_VERBS = re.compile(
    r"(?<![\w./-])(?:mv|rm|rmdir|ln|cp|rsync|chmod|chown|chattr|setfacl|install|tar|find|rename|unlink)(?![\w.-])"
)
PARENT_VERBS = re.compile(r"(?<![\w./-])(?:mv|rm|rmdir|ln|chmod|chown|chattr|setfacl|rename|unlink)(?![\w.-])")
WORD_SPLIT = re.compile(r"[\s\"';&|()<>=,`]+")
BRACES = re.compile(r"\{[^{}]*\}")
WILD = "*?["


def both(path):
    """The path as written and with symlinks followed, each split into its parts."""
    out = []
    for form in (os.path.abspath(path), os.path.realpath(path)):
        parts = [c for c in form.split("/") if c]
        if parts not in out:
            out.append(parts)
    return out


def reason_for(what):
    if what == "self":
        return (
            "BLOCKED: this is the installed copy of list-file-guard.py; a session may not change it. "
            "A person copies hooks in a plain terminal."
        )
    if what == "folders":
        return (
            "BLOCKED: this command changes folder too many times for the merge-list guard to follow. "
            "Split it into separate commands."
        )
    if what in BASE_NAMES:
        return (
            f"BLOCKED: {what} is one of the merge lists; a session may not change or name it "
            "in a shell command. View it with the Read tool. A person edits it in a plain terminal."
        )
    return (
        f"BLOCKED: the {what} folder holds the merge lists or their guard; a session may not move, remove, "
        "link, copy over or re-permission it. A person does that in a plain terminal."
    )


def check_path(tool_input):
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw:
        raw = tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return None
    lexical = os.path.abspath(os.path.expanduser(raw))
    real = os.path.realpath(lexical)
    for protected in PROTECTED + (SELF,):
        if lexical == os.path.abspath(protected) or real == os.path.realpath(protected):
            return "self" if protected == SELF else os.path.basename(protected)
    return None


def expand(word):
    if word == "~" or word.startswith("~/"):
        word = HOME + word[1:]
    return word.replace("${HOME}", HOME).replace("$HOME", HOME)


def parts_match(pattern, target):
    """True when a wildcard path, part by part, could name the target. "**" matches any depth."""
    for i, piece in enumerate(pattern):
        if "**" in piece:
            return True
        if i >= len(target) or not fnmatch.fnmatchcase(target[i], piece):
            return False
    return len(pattern) == len(target)


def folders_of(words, cwd):
    """The session's folder plus every folder the command changes into, or None when there are too many."""
    bases = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        bases.append(os.path.normpath(cwd))
    targets = set()
    for i, word in enumerate(words):
        if word not in ("cd", "pushd"):
            continue
        j = i + 1
        while j < len(words) and words[j].startswith("-") and words[j] != "-":
            j += 1
        if j >= len(words):
            continue
        targets.add(j)
        target = expand(words[j])
        new = [os.path.normpath(target)] if os.path.isabs(target) else [
            os.path.normpath(os.path.join(b, target)) for b in bases
        ]
        for folder in new:
            if folder not in bases:
                bases.append(folder)
        if len(bases) > MAX_FOLDERS:
            return None, targets
    return bases, targets


def check_word(word, bases, afa_verb, parent_verb, is_cd_target):
    word = expand(word)
    if not word:
        return None
    wild = any(c in word for c in WILD) or bool(BRACES.search(word))
    candidates = [word] if os.path.isabs(word) else [os.path.join(b, word) for b in bases]
    for candidate in candidates:
        if wild:
            pattern = candidate
            while BRACES.search(pattern):
                pattern = BRACES.sub("*", pattern)
            pattern = os.path.normpath(pattern)
            forms = [[c for c in pattern.split("/") if c]]
            head, tail = os.path.split(pattern)
            if not any(c in head for c in WILD):
                real = [c for c in os.path.join(os.path.realpath(head), tail).split("/") if c]
                if real not in forms:
                    forms.append(real)
            for form in forms:
                for protected in PROTECTED:
                    if any(parts_match(form, t) for t in both(protected)):
                        return os.path.basename(protected)
                if any(parts_match(form, t) for t in both(SELF)):
                    return "self"
                if afa_verb and any(parts_match(form, t) for t in both(AFA)):
                    return ".config/afa"
                if parent_verb and any(parts_match(form, t) for t in both(CONFIG)):
                    return ".config"
                if parent_verb and any(parts_match(form, t) for t in both(HOOKS)):
                    return ".claude/hooks"
            continue
        path = os.path.normpath(candidate)
        if not os.path.lexists(path):
            lexical_only = True
            real = path
        else:
            lexical_only = False
            real = os.path.realpath(path)
        for protected in PROTECTED:
            if real == os.path.realpath(protected) or path == os.path.abspath(protected):
                return os.path.basename(protected)
        if real == os.path.realpath(SELF) or path == os.path.abspath(SELF):
            return "self"
        if is_cd_target or (lexical_only and path not in (AFA, CONFIG, HOOKS)):
            continue
        if afa_verb and (real == os.path.realpath(AFA) or path == AFA):
            return ".config/afa"
        if parent_verb and (real == os.path.realpath(CONFIG) or path == CONFIG):
            return ".config"
        if parent_verb and (real == os.path.realpath(HOOKS) or path == HOOKS):
            return ".claude/hooks"
    return None


def check_bash(command, cwd):
    for name in BASE_NAMES:
        if name in command:
            return name
    try:
        words = [w for w in WORD_SPLIT.split(command) if w]
        bases, cd_targets = folders_of(words, cwd)
        if bases is None:
            return "folders"
        afa_verb = bool(AFA_VERBS.search(command))
        parent_verb = bool(PARENT_VERBS.search(command))
        for i, word in enumerate(words):
            hit = check_word(word, bases, afa_verb, parent_verb, i in cd_targets)
            if hit:
                return hit
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
            hit = check_bash(command, payload.get("cwd"))
    if hit:
        print(reason_for(hit), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
