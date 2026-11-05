#!/usr/bin/env python3
"""The no-AI wall, tier 1: one private list of paths (member personal data) that no vendor model session may touch.

The co-op's rule (4 Nov 2026): members' personal data goes only to a model we run. The list is private
config and never lives in a repo:

  ~/.config/afa/no-ai-paths.txt     one path or glob per line. `*` stays inside one path segment, `**` crosses segments,
                                    `?` is one character, `~/` is the home folder. A match covers everything under it.
                                    Blank lines and lines starting with # are ignored.

A missing list means the wall is off (one warning line per run, from warn_missing). A list that exists but cannot be read
or parsed fails closed: every path then counts as listed. Symlinks are resolved before matching, and the lexical path is
matched too, so neither form gets around the wall. Paths under /mnt/ match without regard to case (Windows drives).

Everything here is a pure function of the one file, so the lane launcher and the tool hook decide the same way.
Command line (used by the bash helper no_ai_wall.sh):

  no_ai_wall.py status                       counts only
  no_ai_wall.py check-path [--cwd D] P...    exit 0 = none listed, 3 = one is listed (or the list is unreadable)
  no_ai_wall.py check-text FILE              exit 3 when the text names a listed path
Environment overrides (tests): NO_AI_PATHS_FILE, NO_AI_REFUSAL_LOG.
"""
import datetime
import fnmatch
import glob as _glob
import os
import re
import shlex
import sys

UNREADABLE = "(unreadable no-AI list)"
GLOB_CHARS = set("*?[")
_warned = set()


def home():
    return os.environ.get("HOME") or os.path.expanduser("~")


def paths_file():
    return os.environ.get("NO_AI_PATHS_FILE") or os.path.join(home(), ".config", "afa", "no-ai-paths.txt")


def refusal_log():
    return os.environ.get("NO_AI_REFUSAL_LOG") or os.path.join(home(), ".claude", "state", "no-ai-refusals.log")


def warn_missing(which, path, stream=None):
    """One warning line per run (per process) for a missing list. The file's name is not private, only its contents."""
    if which in _warned or os.environ.get("NO_AI_QUIET"):  # a shell that already warned once sets NO_AI_QUIET for its later calls
        return
    _warned.add(which)
    (stream or sys.stderr).write("no-ai wall: %s is missing, so the wall is off for it\n" % path)


def _read(path):
    """-> (text, status). status: ok, missing, or bad (exists but is not a readable regular UTF-8 file)."""
    try:
        if not os.path.lexists(path):
            return "", "missing"
        if not os.path.isfile(path):
            return "", "bad"
        with open(path, "rb") as fh:
            raw = fh.read()
        if b"\0" in raw:
            return "", "bad"
        return raw.decode("utf-8"), "ok"
    except (OSError, UnicodeDecodeError):
        return "", "bad"


def expand(path, base=None):
    """~ and $HOME expanded, made absolute against base (default: the current folder), dots collapsed. No symlink resolution."""
    p = path.strip()
    h = home()
    if p == "~" or p.startswith("~/"):
        p = h + p[1:]
    p = p.replace("${HOME}", h).replace("$HOME", h)
    if "$" in p:
        p = os.path.expandvars(p)
    if not os.path.isabs(p):
        p = os.path.join(base or os.getcwd(), p)
    return os.path.normpath(p)


def _segment_regex(seg):
    out = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c == "*":
            if seg[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _flags(pattern):
    """/mnt/ is a Windows drive (drvfs), where /mnt/c/Users and /mnt/C/USERS are one folder: those patterns ignore case."""
    return re.DOTALL | (re.IGNORECASE if pattern.lower().startswith("/mnt/") else 0)


def _pattern_regex(pattern):
    segs = [s for s in pattern.split("/") if s != ""]
    body = "/".join(_segment_regex(s) for s in segs)
    return re.compile(r"/" + body + r"(?:/.*)?\Z", _flags(pattern))


def _resolved_pattern(pattern):
    """The pattern with its leading literal segments run through realpath, so a listed folder reached by a symlinked parent still matches."""
    segs = pattern.split("/")
    lit = []
    for s in segs[1:]:
        if any(ch in GLOB_CHARS for ch in s):
            break
        lit.append(s)
    if not lit:
        return pattern
    prefix = os.path.realpath("/" + "/".join(lit))
    rest = segs[1 + len(lit):]
    return os.path.normpath(prefix + ("/" + "/".join(rest) if rest else ""))


class Entry:
    def __init__(self, raw):
        self.raw = raw
        self.pattern = expand(raw, "/")
        self.variants = [self.pattern]
        res = _resolved_pattern(self.pattern)
        if res != self.pattern:
            self.variants.append(res)
        self.regexes = [_pattern_regex(v) for v in self.variants]
        self.segs = [[_segment_regex(s) for s in v.split("/") if s] for v in self.variants]
        self.segre = [[re.compile(r, _flags(v)) for r in sv] for v, sv in zip(self.variants, self.segs)]

    def covers(self, path):
        return any(rx.match(path) for rx in self.regexes)

    def inside(self, root):
        """True when this entry lies at or below root (root is an ancestor of, or equal to, the listed folder)."""
        rs = [s for s in root.split("/") if s]
        for sv in self.segre:
            if len(rs) <= len(sv) and all(sv[i].fullmatch(rs[i]) for i in range(len(rs))):
                return True
        return False


class PathList:
    def __init__(self, entries=(), status="ok", reason=""):
        self.entries = list(entries)
        self.status = status
        self.reason = reason

    @property
    def on(self):
        return self.status != "missing"

    def _candidates(self, path, cwd=None):
        p = expand(path, cwd)
        out = [p]
        try:
            r = os.path.realpath(p)
        except (OSError, ValueError):
            r = p
        if r != p:
            out.append(r)
        return out

    def match(self, path, cwd=None):
        """The listed entry (as written in the list) that covers path, UNREADABLE when the list is unreadable, else None."""
        if self.status == "bad":
            return UNREADABLE
        if not self.entries or not path:
            return None
        for c in self._candidates(path, cwd):
            for e in self.entries:
                if e.covers(c):
                    return e.raw
        return None

    def contains(self, root, cwd=None):
        """The listed entry lying at or under root, for a search that would walk into it; None when nothing listed is below root."""
        if self.status == "bad":
            return UNREADABLE
        if not self.entries or not root:
            return None
        for c in self._candidates(root, cwd):
            for e in self.entries:
                if e.inside(c):
                    return e.raw
        return None

    def index_of(self, raw):
        for i, e in enumerate(self.entries, 1):
            if e.raw == raw:
                return i
        return 0


def load_paths(path=None):
    path = path or paths_file()
    text, st = _read(path)
    if st != "ok":
        return PathList(status=st, reason="file is %s" % st)
    entries = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if any(ord(ch) < 32 for ch in s) or not (s.startswith("/") or s == "~" or s.startswith("~/") or s.startswith("$HOME/")):
            return PathList(status="bad", reason="line %d is not an absolute or ~/ path" % n)
        try:
            entries.append(Entry(s))
        except re.error:
            return PathList(status="bad", reason="line %d does not parse" % n)
    return PathList(entries)


def wall_on():
    """True when the path list exists (a missing list means the wall is off)."""
    return load_paths().on


# ---- refusal log -----------------------------------------------------------------------------------------------------

def log_refusal(tool, entry):
    """One line per refusal: time, tool, matched entry. Never file content. A logging failure never stops the refusal."""
    try:
        path = refusal_log()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n" % (datetime.datetime.now().astimezone().isoformat(timespec="seconds"), tool, entry))
    except OSError:
        pass


# ---- paths named in free text and in shell commands ------------------------------------------------------------------

_PATH_IN_TEXT = re.compile(r"""(?<![\w.:-])(?:~|\$HOME|\$\{HOME\})?/[^\s'"`;|&<>()\[\]{},]+""")
_RECURSIVE_PROGRAMS = {"rg", "ag", "ack", "find", "fd", "fdfind", "tree", "du", "rsync", "tar", "zip", "locate", "mlocate", "plocate", "scp"}
_GREPS = {"grep", "egrep", "fgrep", "zgrep"}


def text_paths(text):
    return [m.group(0) for m in _PATH_IN_TEXT.finditer(text or "")]


def text_names_listed_path(text, paths):
    """The listed entry that free text (a lane prompt) names, by a path-looking token or by a literal entry; else None."""
    if paths.status == "bad":
        return UNREADABLE
    for tok in text_paths(text):
        hit = paths.match(tok.rstrip(".:"), "/")
        if hit:
            return hit
    low = text or ""
    for e in paths.entries:
        for form in {e.raw, e.pattern}:
            if form and not any(ch in GLOB_CHARS for ch in form) and form.rstrip("/") in low:
                return e.raw
    return None


def _words(command):
    cleaned = re.sub(r"[;|&<>()`]", " ", command.replace("$(", " "))
    try:
        return shlex.split(cleaned)
    except ValueError:
        return cleaned.split()


def _is_recursive(words):
    for i, w in enumerate(words):
        base = os.path.basename(w)
        if base in _RECURSIVE_PROGRAMS:
            return True
        if base in _GREPS and any((x.startswith("-") and not x.startswith("--") and ("r" in x or "R" in x)) or x in ("--recursive", "--dereference-recursive") for x in words[i + 1:]):
            return True
        if base == "git" and "grep" in words[i + 1:i + 4]:
            return True
        if base in ("cp", "mv") and any(x.startswith("-") and not x.startswith("--") and ("r" in x or "R" in x or "a" in x) for x in words[i + 1:]):
            return True
        if base == "ls" and any(x.startswith("-") and not x.startswith("--") and "R" in x for x in words[i + 1:]):
            return True
    return False


def _expand_globs(path):
    if not any(ch in GLOB_CHARS for ch in path):
        return []
    try:
        return _glob.glob(path)[:300]
    except OSError:
        return []


def bash_hits(command, cwd, paths):
    """-> (entry, kind) for the first listed path a shell command touches or the first listed folder its recursive search would
    walk into; kind is "path" or "root". None when clean. Best effort on text: a path built at run time is out of reach, which is why
    the folder the session starts in is checked too."""
    cwd = cwd or os.getcwd()
    words = _words(command)
    recursive = _is_recursive(words)
    seen = []

    def check(tok, base):
        t = tok
        if not t or t.startswith("-") and "=" not in t and "/" not in t:
            return None
        for piece in [t] + [p for p in re.split(r"[=:,]", t) if p and p != t]:
            if not piece or (piece.startswith("-") and "/" not in piece and "~" not in piece):
                continue
            full = expand(piece, base)
            for cand in [full] + _expand_globs(full):
                hit = paths.match(cand, base)
                if hit:
                    return hit, "path"
                if recursive:
                    hit = paths.contains(cand, base)
                    if hit:
                        return hit, "root"
        return None

    cur = cwd
    i = 0
    while i < len(words):
        w = words[i]
        r = check(w, cur) or (check(w, cwd) if cur != cwd else None)
        if r:
            return r
        if w == "cd" and i + 1 < len(words):
            nxt = expand(words[i + 1], cur)
            cur = nxt
        i += 1
    if recursive:  # a search with no folder named walks the working folder
        hit = paths.contains(cwd, cwd)
        if hit:
            return hit, "root"
    for tok in text_paths(command):
        hit = paths.match(tok.rstrip(".:"), cwd)
        if hit:
            return hit, "path"
        if recursive:
            hit = paths.contains(expand(tok, cwd), cwd)
            if hit:
                return hit, "root"
    return None


_PATTERN_CHARS = GLOB_CHARS | {"{"}
_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_READ_ONLY_PROGRAMS = {"ls", "stat", "wc", "file", "sha256sum", "md5sum", "test", "["}


def pattern_start(pattern, root, cwd):
    """The folder a Glob pattern starts from, or None when it cannot be worked out. A relative pattern is joined with the search
    root (not the session folder), `~` and `$HOME` are expanded, and `..` is collapsed before the first wildcard. A `..` AFTER a
    wildcard could climb anywhere (`*/../..`), so it returns None and the caller refuses."""
    p = (pattern or "").strip()
    if p == "~" or p.startswith(("~/", "$HOME", "${HOME}")):
        p = expand(p, cwd)
    elif not os.path.isabs(p):
        p = os.path.join(root, p)
    lit, wild = [], False
    for seg in p.split("/"):
        if any(ch in _PATTERN_CHARS for ch in seg):
            wild = True
        elif wild and seg == "..":
            return None
        if not wild:
            lit.append(seg)
    return os.path.normpath("/".join(lit) or "/")


def protected_files():
    """Files no session may change: the list itself, the hook and the module (and its bash helper). A session that could edit
    them could turn the wall off. They are checked by realpath, so a symlink to one counts too."""
    h = home()
    return [paths_file()] + [os.path.join(h, ".claude", *rel) for rel in (
        ("hooks", "no-ai-wall-guard.py"), ("scripts", "no_ai_wall.py"), ("scripts", "no_ai_wall.sh"))]


def _is_protected(path, cwd, which):
    cand = PathList()._candidates(path, cwd)
    for f in which:
        forms = {os.path.normpath(f), os.path.realpath(f)}
        if any(c in forms for c in cand):
            return True
    return False


def _reaches(path, cwd, files):
    """True when path is one of files or a folder above one (a search rooted there would read the file). Symlinks count."""
    cand = PathList()._candidates(path, cwd)
    for f in files:
        for form in {os.path.normpath(f), os.path.realpath(f)}:
            for c in cand:
                if form == c or form.startswith(c.rstrip("/") + "/"):
                    return True
    return False


_CODE_SUFFIXES = (".py", ".sh")


def _is_guarded_config(path, cwd):
    """True for a file a session could use to switch the wall off without touching the wall's own files: any .py or .sh under
    ~/.claude/hooks or ~/.claude/scripts (a script the hook imports, or one a hook runs), and the settings*.json files that register the
    hook, in ~/.claude and in any project's .claude folder. Writes only; reading and running them stays open."""
    base = os.path.join(home(), ".claude")
    folders = set()
    for sub in ("hooks", "scripts"):
        d = os.path.join(base, sub)
        folders.update({os.path.normpath(d), os.path.realpath(d)})
    for c in PathList()._candidates(path, cwd):
        if c.lower().startswith("/mnt/"):  # a Windows drive: /mnt/c/Users/X/.CLAUDE/Settings.JSON is the same file
            c, folders_c = c.lower(), {d.lower() for d in folders}
        else:
            folders_c = folders
        name = os.path.basename(c)
        if name.endswith(_CODE_SUFFIXES) and any(c.startswith(d.rstrip("/") + "/") for d in folders_c):
            return True
        if fnmatch.fnmatchcase(name, "settings*.json") and os.path.basename(os.path.dirname(c)) == ".claude":
            return True
    return False


def guarded_places():
    """The files and folders whose loss would unguard the wall: the protected files, the hooks and scripts folders, and the home .claude
    folder with its settings. An operand of rm, mv or chmod that IS one of them or lies above one (renaming the folder away) is a write."""
    base = os.path.join(home(), ".claude")
    return protected_files() + [os.path.join(base, "hooks"), os.path.join(base, "scripts"), os.path.join(base, "settings.json"), os.path.join(base, "settings.local.json")]


_WRITE_PROGRAMS = {"rm", "unlink", "shred", "truncate", "tee", "touch", "chmod", "chown", "chgrp", "mv", "dd", "patch", "sponge"}
_DEST_LAST_PROGRAMS = {"cp", "install", "rsync", "ln"}
_INLINE_PROGRAM = re.compile(r"(?:python[\d.]*|node|perl|ruby|bash|sh|zsh|php|deno|bun)\Z")
_REDIRECT = re.compile(r"(?:\d*|&)>>?\|?\s*([^\s;|&<>()`]+)")


def bash_writes_guarded(command, cwd):
    """True when a shell command (best effort, from its text) writes a file _is_guarded_config names: a redirection into it, a
    program that changes its arguments (rm, tee, sed -i, perl -i, mv, chmod ...), the destination of cp, install, rsync or ln, or an
    inline interpreter (python -c, node -e, bash -c) that mentions one. A command that only reads or runs such a file is not stopped."""
    cur = cwd or os.getcwd()
    for seg in _segments(command.replace(">|", ">")):  # `>|` is a plain redirect; the pipe split would cut it in two
        for m in _REDIRECT.finditer(seg):
            if _is_guarded_config(m.group(1).strip("'\""), cur):
                return True
        words = _words(seg)
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            cur = expand(words[1], cur)
            continue
        i = 0
        while i < len(words) and (words[i] in ("sudo", "env", "command", "nohup", "time", "exec") or re.fullmatch(r"\w+=\S*", words[i])):
            i += 1
        if i >= len(words):
            continue
        prog = os.path.basename(words[i])
        args = words[i + 1:]
        operands = [a for a in args if a and not (a.startswith("-") and "/" not in a)]

        def guarded(arg):
            return _is_guarded_config(arg.split("=")[-1], cur) or _is_guarded_config(arg, cur)

        def into_guarded_folder(arg):  # a copy into the hooks or scripts folder, whatever the file is called
            return (arg.endswith("/") or os.path.isdir(expand(arg, cur))) and _is_guarded_config(os.path.join(arg, "x.py"), cur)

        if prog in _DEST_LAST_PROGRAMS:
            into = [nxt for a, nxt in zip(args, args[1:]) if a in ("-t", "--target-directory")]  # always a folder
            into += [a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")] + [a[2:] for a in args if a.startswith("-t") and len(a) > 2 and not a.startswith("--")]
            if any(_is_guarded_config(os.path.join(d, "x.py"), cur) for d in into):
                return True
            if not into and operands[-1:] and (guarded(operands[-1]) or into_guarded_folder(operands[-1])):
                return True
            continue
        if _INLINE_PROGRAM.match(prog) and any(a in ("-c", "-e", "-r", "-E", "--eval") for a in args):
            if any(guarded(t) for a in args for t in text_paths(a)):  # code in the command line that names one of them
                return True
        if prog in _WRITE_PROGRAMS and any(_reaches(a, cur, guarded_places()) for a in operands):
            return True  # `mv ~/.claude/hooks /tmp/x` or `rm -r ~/.claude` unguards the wall as surely as editing a file
        if not any(guarded(a) for a in operands):
            continue
        if prog in _WRITE_PROGRAMS:
            return True
        if prog in ("sed", "gsed", "perl", "ruby") and any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and "i" in a) for a in args):
            return True
    return False


def _segments(command):
    return [seg for seg in re.split(r"\|\||&&|[;|&\n]", command) if seg.strip()]


def _read_only_listing(command):
    """True for a command that only looks at a file's existence or size, or reads the list through redact_view.py, or runs the module's own check commands (no redirection,
    no command substitution, no program outside the short list)."""
    if re.search(r"[<>`]|\$\(", command):
        return False
    for seg in _segments(command):
        words = _words(seg)
        if not words:
            continue
        prog = os.path.basename(words[0])
        if prog in _READ_ONLY_PROGRAMS:
            continue
        if prog.startswith("python"):
            rest = [w for w in words[1:] if not w.startswith("-")]
            ok = [os.path.realpath(os.path.join(home(), ".claude", "scripts", n)) for n in ("redact_view.py", "no_ai_wall.py")]  # run, not edited
            if rest and os.path.realpath(expand(rest[0], "/")) in ok:
                continue
        return False
    return True


def _command_tokens(command, cwd):
    out = []
    for w in _words(command) + text_paths(command):
        for piece in [w] + [p for p in re.split(r"[=:,]", w) if p and p != w]:
            if "/" in piece or piece.startswith("~") or piece.startswith("$"):
                full = expand(piece, cwd)
                out.append(full)
                out.extend(_expand_globs(full))
    return out


def _path_shaped(text):
    """A string an MCP tool could mean as a file: it has a slash, or starts with a dot or a ~ or $HOME. Short, one-line strings only."""
    return bool(text) and len(text) < 4096 and "\n" not in text and ("/" in text or text.startswith((".", "~", "$HOME", "${HOME}")))


def reads_content_recursively(words):
    """True for a shell command that reads or copies the CONTENT of every file under the folders it is given (a name-only listing such
    as `find -name`, `tree` or `du` is not one)."""
    for i, w in enumerate(words):
        base = os.path.basename(w)
        rest = words[i + 1:]
        if base in ("rg", "ag", "ack", "tar", "zip", "rsync", "scp"):
            return True
        if base in ("find", "fd", "fdfind") and any(x in ("-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fls", "-x", "--exec", "-X", "--exec-batch") for x in rest):
            return True
        if base in _GREPS or base in ("cp", "mv") or base == "git":
            if _is_recursive(words[i:i + 1] + rest) and (base != "git" or "grep" in rest[:3]):
                return True
    return False


def _strings(value, depth=0):
    """Every string inside a tool's input, however deeply nested (an MCP tool's arguments have no fixed shape)."""
    if depth > 6:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v, depth + 1)


def evaluate(payload, paths=None):
    """The hook's decision for one tool call. -> None to allow, else (tool, entry, message) to deny. `entry` is "entry N" (the list
    position), never the listed path: the refusal reaches a model's context and the log is a file a model could open."""
    paths = paths or load_paths()
    if not paths.on:  # no list at all: the wall is off. A list with no entries is still a wall, and still guards its own files.
        return None
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    cwd = payload.get("cwd") or os.getcwd()

    if paths.status == "bad":
        return (tool, UNREADABLE, "no-ai wall: the no-AI path list exists but cannot be read (%s), so every tool is refused until it is fixed outside the model." % paths.reason)

    def label(entry):
        n = paths.index_of(entry)
        return "entry %d" % n if n else "entry ?"

    # The session's own folder comes first and covers EVERY tool, MCP and unknown ones included.
    hit = paths.match(cwd, "/")
    if hit:
        return (tool, label(hit), "no-ai wall: this session's working folder is on the no-AI list (%s). Start a clean room instead: lane.sh --clean-room <scratch-folder> ..." % label(hit))

    def deny_path(entry):
        return (tool, label(entry), "no-ai wall: that path is on the no-AI list (%s) and may not be opened, searched or changed in this session. If the task needs it, work from a clean room (lane.sh --clean-room) and leave that folder out of the prompt." % label(entry))

    def deny_root(entry):
        return (tool, label(entry), "no-ai wall: this search would walk into a folder on the no-AI list (%s). Narrow the search root so it leaves that folder out." % label(entry))

    def deny_protected():
        return (tool, "protected file", "no-ai wall: the no-AI list, its hook and its module, the hook and script files beside them and the settings files cannot be changed from a session. Edit them outside the model.")

    def deny_list_search():
        return (tool, "protected file", "no-ai wall: the no-AI list is private config and this search would read it. Narrow the search root to the folder you need.")

    def reads_list(root):
        return _reaches(root, cwd, [paths_file()])

    def is_guarded(path):
        return _is_protected(path, cwd, protected_files()) or _is_guarded_config(path, cwd)

    def check_pattern(pat, root):
        start = pattern_start(pat, root, cwd)
        if start is None:
            return (tool, "pattern", "no-ai wall: this pattern climbs out of its folder after a wildcard, so it cannot be checked and is refused. Name the folder first.")
        h = paths.match(start, cwd)
        if h:
            return deny_path(h)
        h = paths.contains(start, cwd)
        if h:
            return deny_root(h)
        if reads_list(start):
            return deny_list_search()
        return None

    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        p = ti.get("file_path") or ti.get("notebook_path")
        if p:
            h = paths.match(str(p), cwd)
            if h:
                return deny_path(h)
            if tool in _WRITE_TOOLS and is_guarded(str(p)):
                return deny_protected()
            if tool == "Read" and _is_protected(str(p), cwd, [paths_file()]):
                return deny_protected()
        return None
    if tool in ("Grep", "Glob"):
        root = str(ti.get("path") or cwd)
        h = paths.match(root, cwd)
        if h:
            return deny_path(h)
        h = paths.contains(root, cwd)
        if h:
            return deny_root(h)
        if reads_list(root):  # the list is private config: a search rooted on it, or on a folder above it, would read it
            return deny_list_search()
        base = expand(root, cwd)
        for pat in (ti.get("pattern") if tool == "Glob" else None, ti.get("glob")):
            if pat:
                r = check_pattern(str(pat), base)
                if r:
                    return r
        return None
    if tool == "Bash":
        command = str(ti.get("command") or "")
        r = bash_hits(command, cwd, paths)
        if r:
            return deny_root(r[0]) if r[1] == "root" else deny_path(r[0])
        if any(_is_protected(t, cwd, protected_files()) for t in _command_tokens(command, cwd)) and not _read_only_listing(command):
            return deny_protected()
        if bash_writes_guarded(command, cwd):
            return deny_protected()
        words = _words(command)
        if reads_content_recursively(words):  # `grep -r`, `rg`, `tar` and the like over the list's folder or one above it
            roots = _command_tokens(command, cwd) or [cwd]
            if any(reads_list(t) for t in roots):
                return deny_list_search()
        return None

    # An MCP tool or any tool this module does not know: its arguments could name a listed path in any field, so every string
    # in them is read as a path or as text that names one.
    for text in _strings(ti):
        pieces = [text] + [t.strip("'\"`,;:()[]{}<>") for t in text[:20000].split()[:400]]  # a sentence naming a relative path; capped
        for piece in pieces:
            if not _path_shaped(piece):
                continue
            h = paths.match(piece, cwd)  # a relative one is joined with the session folder, as the shell tools do
            if h:
                return deny_path(h)
            if is_guarded(piece):
                return deny_protected()
        h = text_names_listed_path(text, paths)
        if h:
            return deny_path(h)
        if any(is_guarded(t) for t in text_paths(text)):
            return deny_protected()
    return None


# ---- command line ----------------------------------------------------------------------------------------------------

def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    pl = load_paths()
    if cmd == "status":
        print("paths: %s%s" % (pl.status, ", %d entries" % len(pl.entries) if pl.status == "ok" else (" (%s)" % pl.reason if pl.reason else "")))
        return 0
    if cmd == "check-path":
        cwd = None
        if rest[:1] == ["--cwd"]:
            cwd, rest = rest[1], rest[2:]
        if not pl.on:
            warn_missing("paths", paths_file())
            return 0
        for p in rest:
            hit = pl.match(p, cwd)
            if hit:
                if hit == UNREADABLE:
                    sys.stderr.write("no-ai wall: the path list is unreadable (%s); everything is refused until it is fixed\n" % pl.reason)
                else:
                    sys.stderr.write("no-ai wall: %s is on the no-AI list (entry %d)\n" % (p, pl.index_of(hit)))
                return 3
        return 0
    if cmd == "check-text":
        if not pl.on:
            warn_missing("paths", paths_file())
            return 0
        try:
            text = open(rest[0], encoding="utf-8", errors="replace").read()
        except OSError:
            sys.stderr.write("no-ai wall: cannot read %s\n" % rest[0])
            return 3
        hit = text_names_listed_path(text, pl)
        if hit:
            sys.stderr.write("no-ai wall: the prompt names a path on the no-AI list (entry %d)\n" % pl.index_of(hit) if hit != UNREADABLE else "no-ai wall: the path list is unreadable\n")
            return 3
        return 0
    sys.stderr.write("unknown command: %s\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
