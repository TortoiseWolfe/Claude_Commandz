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

Where it lives: ~/.claude/no-ai-wall/ holds only the wall's own files (this module, no_ai_wall.sh, no-ai-wall-guard.py, wall_cleanup.py). The tamper
guard below covers that folder, the list, and the settings*.json files, and nothing else under ~/.claude, so ordinary tooling in
~/.claude/scripts and ~/.claude/hooks installs without anyone's hands. The hook loads this file by absolute path from its own folder.
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


WALL_FOLDER = "no-ai-wall"
_WALL_FILES = ("no-ai-wall-guard.py", "no_ai_wall.py", "no_ai_wall.sh", "wall_cleanup.py")


def wall_dir():
    """The live folder that holds only the wall's own files."""
    return os.path.join(home(), ".claude", WALL_FOLDER)


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


def _is_mnt(path):
    return path[:5].lower() == "/mnt/"


def _fold(path):
    """The one normaliser for Windows drives. /mnt/ is a drvfs mount, where /mnt/c/Users and /mnt/C/USERS are one folder, so every
    comparison of two paths runs both through here (_is_protected, _reaches, _is_guarded_config, the guarded-place checks)."""
    return path.lower() if _is_mnt(path) else path


def _flags(pattern):
    """Patterns under /mnt/ ignore case, for the same reason as _fold."""
    return re.DOTALL | (re.IGNORECASE if _is_mnt(pattern) else 0)


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
        words = shlex.split(cleaned)
    except ValueError:
        return cleaned.split()
    out = []
    for w in words:  # `bash -c 'find /x'` is one quoted word: its own words count too, so a search inside it is still a search
        out.append(w)
        if any(ch.isspace() for ch in w):
            out.extend(_words(w))
    return out


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


def _expand_globs(path, limit=20000):
    """What a wildcard path matches on disk. The cap is far above any real folder: a short cap could cut a guarded file off the list."""
    if not any(ch in GLOB_CHARS for ch in path):
        return []
    out = []
    try:
        for hit in _glob.iglob(path):
            out.append(hit)
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


def bash_hits(command, cwd, paths):
    """-> (entry, kind) for the first listed path a shell command touches or the first listed folder its recursive search would
    walk into; kind is "path" or "root". None when clean. Best effort on text: a path built at run time is out of reach, which is why
    the folder the session starts in is checked too.

    A segment that only starts a lane (lane.sh) is not a search (issue 233): its folder argument is a working folder, which the launcher
    itself refuses when it is listed and whose tool calls the lane's own hook guards. Such a segment still counts for listed paths, but
    it neither makes the command a search nor has its operands walked as search roots."""
    cwd = cwd or os.getcwd()
    segs = _segments(command)
    if any(_is_launcher(seg) for seg in segs):
        r = _bash_hits(command, cwd, paths, searching=False)
        if r:
            return r
        command = " ; ".join(seg for seg in segs if not _is_launcher(seg))
    return _bash_hits(command, cwd, paths)


def _bash_hits(command, cwd, paths, searching=True):
    words = _words(command)
    recursive = searching and _is_recursive(words)
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
    """Files no session may change: the list itself, and the hook, the module, its bash helper and the cleanup command in the wall's own folder. The three places
    the wall lived before that folder existed count too, while a file is still there (a machine not yet moved runs from them); once the
    install has moved them away, ~/.claude/scripts and ~/.claude/hooks hold no wall file and are ordinary folders. A session that could edit
    a protected file could turn the wall off. They are checked by realpath, so a symlink to one counts too."""
    base = os.path.join(home(), ".claude")
    legacy = [os.path.join(base, "hooks", "no-ai-wall-guard.py"), os.path.join(base, "scripts", "no_ai_wall.py"), os.path.join(base, "scripts", "no_ai_wall.sh")]
    return [paths_file()] + [os.path.join(wall_dir(), n) for n in _WALL_FILES] + [f for f in legacy if os.path.lexists(f)]


def _is_protected(path, cwd, which):
    cand = [_fold(c) for c in PathList()._candidates(path, cwd)]
    for f in which:
        forms = {_fold(os.path.normpath(f)), _fold(os.path.realpath(f))}
        if any(c in forms for c in cand):
            return True
    return False


def _reaches(path, cwd, files):
    """True when path is one of files or a folder above one (a search rooted there would read the file). Symlinks count."""
    cand = [_fold(c) for c in PathList()._candidates(path, cwd)]
    for f in files:
        for form in {_fold(os.path.normpath(f)), _fold(os.path.realpath(f))}:
            for c in cand:
                if form == c or form.startswith(c.rstrip("/") + "/"):
                    return True
    return False


def _is_guarded_config(path, cwd):
    """True for a file a session could use to switch the wall off: anything inside the wall's own folder (~/.claude/no-ai-wall, any name, new
    files too) and the settings*.json files that register the hook, in ~/.claude and in any project's .claude folder. Other files under
    ~/.claude/scripts and ~/.claude/hooks are ordinary tooling and are not guarded: the hook compiles the module from the wall's folder by
    absolute path, so nothing beside it can stand in for it. Writes only; reading and running them stays open."""
    folders = {os.path.normpath(wall_dir()), os.path.realpath(wall_dir())}
    folders_c = {_fold(d) for d in folders}
    for c in PathList()._candidates(path, cwd):
        c = _fold(c)  # a Windows drive: /mnt/c/Users/X/.CLAUDE/Settings.JSON is the same file
        name = os.path.basename(c)
        if any(c.startswith(d.rstrip("/") + "/") for d in folders_c):
            return True
        if fnmatch.fnmatchcase(name, "settings*.json") and os.path.basename(os.path.dirname(c)) == ".claude":
            return True
    return False


def guarded_places():
    """The files and folders whose loss would unguard the wall: the protected files, the wall's own folder, and the home settings files.
    An operand of rm, mv or chmod that IS one of them or lies above one (renaming ~/.claude away) is a write."""
    base = os.path.join(home(), ".claude")
    return protected_files() + [wall_dir(), os.path.join(base, "settings.json"), os.path.join(base, "settings.local.json")]


_WRITE_PROGRAMS = {"rm", "unlink", "shred", "truncate", "tee", "touch", "chmod", "chown", "chgrp", "mv", "dd", "patch", "sponge"}
_DEST_LAST_PROGRAMS = {"cp", "install", "rsync", "ln"}
_FIND_PROGRAMS = {"find", "fd", "fdfind"}
_FIND_WRITE_ACTIONS = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls", "-x", "-X", "--exec", "--exec-batch")
_INLINE_PROGRAM = re.compile(r"(?:python[\d.]*|node|perl|ruby|bash|sh|zsh|php|deno|bun)\Z")
_REDIRECT = re.compile(r"(?:\d*|&)>>?\|?\s*([^\s;|&<>()`]+)")
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
_WRAPPERS = ("sudo", "doas", "env", "command", "nohup", "time", "exec", "timeout", "nice", "ionice", "setsid", "stdbuf", "xargs", "flock")


_SEQUENCE = re.compile(r"\{(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.(-?\d+))?\}")


def _sequence(m):
    """The words of one `{1..3}` or `{a..e}` brace sequence, or None when it is too long to list."""
    a, b, step = m.group(1), m.group(2), abs(int(m.group(3) or 1)) or 1
    if a.lstrip("-").isdigit() != b.lstrip("-").isdigit():
        return None  # the shell leaves a mixed sequence as it is
    if a.lstrip("-").isdigit():
        lo, hi = int(a), int(b)
        if abs(hi - lo) // step > 256:
            return None
        return [str(n) for n in (range(lo, hi + 1, step) if hi >= lo else range(lo, hi - 1, -step))]
    lo, hi = ord(a), ord(b)
    return [chr(n) for n in (range(lo, hi + 1, step) if hi >= lo else range(lo, hi - 1, -step))]


def _brace_expand(word, limit=256):
    """-> (words, complete): the words the shell makes of `a{b,c}d` and `a{1..3}`. `complete` is False when the cap cut the list short or a
    brace form was left unexpanded; the caller then fails closed, because a guarded name could hide in what was not listed."""
    out, complete = [word], True
    for _ in range(12):
        nxt, changed = [], False
        for x in out:
            m = _BRACES.search(x)
            if m:
                changed = True
                nxt.extend(x[:m.start()] + alt + x[m.end():] for alt in m.group(1).split(","))
                continue
            m = _SEQUENCE.search(x)
            seq = _sequence(m) if m else None
            if m and seq is not None:
                changed = True
                nxt.extend(x[:m.start()] + s + x[m.end():] for s in seq)
                continue
            if m:
                complete = False
            nxt.append(x)
        if len(nxt) > limit:
            complete = False
        out = nxt[:limit]
        if not changed:
            break
    else:
        complete = False
    if any(_BRACES.search(x) or _SEQUENCE.search(x) for x in out):
        complete = False
    return out, complete


def _glob_matches(pattern, path):
    """Whole-path match of a wildcard pattern: `*`, `?` and `[..]` stay inside one segment, a segment that is `**` crosses segments."""
    ps = [x for x in _fold(pattern).split("/") if x]
    xs = [x for x in _fold(path).split("/") if x]

    def go(i, j):
        if i == len(ps):
            return j == len(xs)
        if ps[i] == "**":
            return any(go(i + 1, k) for k in range(j, len(xs) + 1))
        return j < len(xs) and fnmatch.fnmatchcase(xs[j], ps[i]) and go(i + 1, j + 1)
    return go(0, 0)


def _glob_reaches(pattern, files):
    """True when a wildcard pattern could name one of files or a folder above one, whether or not anything matches on disk now: the
    pattern is checked against every ancestor of every file, so a wildcard that is only a prefix of a guarded name still counts."""
    for f in files:
        for form in {os.path.normpath(f), os.path.realpath(f)}:
            segs = [x for x in form.split("/") if x]
            for k in range(1, len(segs) + 1):
                if _glob_matches(pattern, "/" + "/".join(segs[:k])):
                    return True
    return False


def _guarded_folders():
    return [wall_dir()]


def _operand_forms(arg, cur):
    """-> [(path, pattern)]: every absolute path an operand could mean. Brace alternatives and the part after `=` (--opt=PATH) are tried,
    and a wildcard gives its pattern plus what it matches on disk (pattern is the wildcard form the hit came from)."""
    out = []
    words, complete = _brace_expand(arg)
    for b in words:
        for piece in ([b, b.split("=")[-1]] if "=" in b else [b]):
            full = expand(piece, cur)
            out.append((full, full))
            out.extend((hit, full) for hit in _expand_globs(full))
    return out, complete


def _touches(arg, cur, places, folders=False, is_dir=False):
    """True when an operand is, lies above, or (a wildcard) could be a guarded file or one of `places`. `folders` also asks whether it is
    a folder that holds guarded files (a copy lands inside it); `is_dir` says the operand is known to be a folder (a -t target). An
    operand whose braces cannot be listed in full counts as touching: it fails closed."""
    forms, complete = _operand_forms(arg, cur)
    if not complete:
        return True
    for path, pattern in forms:
        if _is_guarded_config(path, cur) or (places and _reaches(path, cur, places)):
            return True
        if folders and (is_dir or arg.endswith("/") or os.path.isdir(path)) and _is_guarded_config(os.path.join(path, "x.py"), cur):
            return True
        if path == pattern and any(ch in GLOB_CHARS for ch in path):
            if (places and _glob_reaches(path, places)) or any(_glob_matches(path, os.path.join(f, n)) for f in _guarded_folders() for n in ("x.py", "x.sh")):
                return True
    return False


def _short_flags(args):
    return {ch for a in args if a.startswith("-") and not a.startswith("--") for ch in a[1:]}


def _copy_targets(prog, args):
    """The folders named by -t / --target-directory in every form GNU getopt takes: `-t D`, `-tD`, `-at D`, `-vtD`, `--target-directory D`,
    `--target-directory=D` and any prefix of it (`--targ=D`, `--t D`). None of them is a last operand, so a last-operand rule misses them."""
    out = []
    if prog == "rsync":  # rsync has no such option: its -t means "keep times"
        return out
    for i, a in enumerate(args):
        if a == "--":
            break
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name and "target-directory".startswith(name):
                out.append(val if eq else nxt)
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                if ch in "tSmog":  # these take a value: the rest of the word, or the next word
                    if ch == "t":
                        out.append(a[j + 1:] or nxt)
                    break
    return out


def _option_after_operand(args):
    """True when an option follows an operand-looking word. GNU getopt lets options come after the operands, and an option may take the
    next word as its value (`cp a ~/.claude/scripts/x.py -S .bak` ends in a value, not in the destination), so the last operand cannot be
    trusted as the destination and every operand is checked as one instead. Fails closed for every option of every program."""
    seen = False
    for a in args:
        if a == "--":
            break
        if a.startswith("-") and len(a) > 1:
            if seen:
                return True
        else:
            seen = True
    return False


_ALL_PROGRAMS = _WRITE_PROGRAMS | _DEST_LAST_PROGRAMS | _FIND_PROGRAMS | {"sed", "gsed", "perl", "ruby"}
_RSYNC_VALUED = ("log-file", "backup-dir", "temp-dir", "partial-dir", "write-batch", "only-write-batch", "compare-dest", "copy-dest",
                 "link-dest", "files-from", "exclude-from", "include-from", "config", "rsh", "rsync-path", "out-format", "log-file-format")


def _known_program(word):
    name = os.path.basename(word)
    return name in _ALL_PROGRAMS or bool(_INLINE_PROGRAM.match(name))


def _skip_wrappers(words, scan=True):
    """Index of the program a command really runs, past sudo, env, VAR=x and the like. A wrapper's own options and values (`sudo -u root rm`,
    `env -C dir find`, `timeout 5 rm`) are skipped too: the program is the first known one after them, inline interpreters included."""
    i = 0
    while i < len(words) and (words[i] in _WRAPPERS or re.fullmatch(r"\w+=\S*", words[i])):
        i += 1
    if scan and i and i < len(words) and not _known_program(words[i]) and re.fullmatch(r"\w+=\S*", words[i - 1]) is None:
        for k in range(i, len(words)):
            if _known_program(words[k]):
                return k
    return i


def _rsync_values(args):
    """The words rsync reads as a file to write or a folder to fill, besides its last operand: the value of `--log-file=F` or `--log-file F`
    (any unambiguous prefix of the option, as rsync takes it) and `-T DIR`. Over-reading is the safe side: every word returned is checked
    as a destination."""
    out = []
    for i, a in enumerate(args):
        if a == "--":
            break
        nxt = args[i + 1] if i + 1 < len(args) else ""
        if a.startswith("--"):
            name, eq, val = a[2:].partition("=")
            if name and name not in ("exclude", "include", "filter") and any(v.startswith(name) for v in _RSYNC_VALUED):
                out.append(val if eq else nxt)
        elif a.startswith("-") and len(a) > 1 and "T" in a[1:]:
            out.append(a[a.index("T") + 1:] or nxt)
    return out


def _changes_folder(words):
    """True for a command line that moves the working folder with a wrapper option (`env -C dir`, `env --chdir=dir`)."""
    return "env" in words and any(w == "-C" or w == "--chdir" or w.startswith(("--chdir=", "-C")) for w in words)


def _is_launcher(seg):
    """True for one shell segment that only starts a lane with lane.sh (which applies the wall itself and gives the lane its own hook). No
    substitution or grouping, so no search can hide in it."""
    if re.search(r"`|\$\(|[()<]", seg):
        return False
    words = _words(seg)
    i = _skip_wrappers(words, scan=False)  # a wrapper option before lane.sh means this is not a plain launch
    if i < len(words) and os.path.basename(words[i]) in ("bash", "sh"):
        i += 1
        if i < len(words) and words[i].startswith("-"):
            return False
    return i < len(words) and os.path.basename(words[i]) == "lane.sh"


def bash_writes_guarded(command, cwd):
    """True when a shell command (best effort, from its text) writes a file _is_guarded_config names or empties a folder that holds them: a
    redirection into one, a program that changes its arguments (rm, tee, sed -i, perl -i, mv, chmod ...), the destination of cp, install,
    rsync or ln (named by position or by any -t form), find or fd with -delete or -exec, or an inline interpreter (python -c, node -e, bash -c)
    that mentions one. Operands are read after brace and wildcard expansion. A command that only reads or runs such a file is not stopped."""
    start = cwd or os.getcwd()
    cur = start  # None once a directory change cannot be worked out from the text (cd -, popd, an unset variable, a substitution)
    places = guarded_places()

    def unknown(a):  # a relative operand after a directory change the text cannot resolve: it fails closed
        return cur is None and not a.startswith(("/", "~", "$"))

    for seg in _segments(command.replace(">|", ">")):  # `>|` is a plain redirect; the pipe split would cut it in two
        for m in _REDIRECT.finditer(seg):
            tgt = m.group(1).strip("'\"")
            if unknown(tgt) or _touches(tgt, cur or start, []):
                return True
        words = _words(seg)
        if not words:
            continue
        w0 = words[0]
        if w0 in ("builtin", "command") and words[1:2] == ["cd"]:
            words, w0 = words[1:], "cd"
        if w0 in ("cd", "pushd", "chdir", "popd"):
            arg = next((a for a in words[1:] if a == "-" or not a.startswith("-")), None)
            if w0 == "popd" or arg == "-" or re.search(r"`|\$\(", seg):
                cur = None
            elif arg is None or arg == "~":
                cur = home()
            else:
                new = expand(arg, cur or start)
                cur = None if "$" in new else new
            continue
        if any(os.path.basename(x) in ("cd", "pushd", "popd", "chdir") for x in words):
            cur = None  # a directory change behind a keyword, brace, wrapper or assignment (`then cd x`, `{ cd x; }`, `time cd x`)
        i = _skip_wrappers(words)
        if i >= len(words):
            continue
        seg_cur = None if _changes_folder(words[:i]) else cur
        prog = os.path.basename(words[i])
        args = words[i + 1:]
        operands = [a for a in args if a and not (a.startswith("-") and "/" not in a)]
        base = seg_cur or start

        def lost(a):  # no known working folder for this segment, and the operand is relative
            return seg_cur is None and not a.startswith(("/", "~", "$"))

        if prog in _DEST_LAST_PROGRAMS:
            flags = _short_flags(args)
            recursive = prog == "rsync" or bool(flags & set("rRa")) or any(a.startswith(("--r", "--a")) for a in args)
            every = any(a.startswith(("--delete", "--del", "--remove")) for a in args) or (prog != "rsync" and "T" in flags)
            targets = _copy_targets(prog, args)
            extra = _rsync_values(args) if prog == "rsync" else []
            if every:
                dests = operands + targets
            elif targets:
                dests = targets
            elif _option_after_operand(args):
                dests = operands
            else:
                dests = operands[-1:]
            dests = dests + extra + [x.split("=", 1)[1] for x in args if x.startswith("--") and "=" in x]  # a file an option writes
            # The destination (every operand when a delete, remove or -T option is on) may not be a guarded file, lie in a guarded folder,
            # or, for a recursive copy, rsync, ln or a delete, be a folder that holds one: `rsync --delete empty/ ~/.claude/` empties the hooks.
            reach = places if (recursive or prog == "ln" or every) else [p for p in places if os.path.isfile(p) or os.path.islink(p)]
            if any(lost(d) or _touches(d, base, reach, folders=True, is_dir=bool(targets)) for d in dests):
                return True
            continue  # the sources of a copy are only read
        if prog in _FIND_PROGRAMS and any(a in _FIND_WRITE_ACTIONS for a in args):
            roots = list(operands)  # a root, or the file an action writes
            if prog != "find" or not [a for a in args[:1] if not a.startswith(("-", "!", "(", ","))]:
                if seg_cur is None:
                    return True  # find with no root starts in a working folder the text cannot name
                roots.append(seg_cur)  # find with no root starts in the working folder; so does fd, whose path argument is optional
            if any(lost(a) or _touches(a, base, places, folders=True) for a in roots):
                return True
        if _INLINE_PROGRAM.match(prog) and any(a in ("-c", "-e", "-r", "-E", "--eval") for a in args):
            if (seg_cur is None and any("/" in a for a in args if not a.startswith("-"))) or any(_touches(t, base, []) for a in args for t in text_paths(a)):  # code in the command line that names one of them
                return True
        if prog in _WRITE_PROGRAMS and any(lost(a) or _touches(a, base, places) for a in operands):
            return True  # `mv ~/.claude/hooks /tmp/x` or `rm -r ~/.claude` unguards the wall as surely as editing a file
        if prog in ("sed", "gsed", "perl", "ruby") and any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--") and "i" in a) for a in args):
            if any(lost(a) or _touches(a, base, []) for a in operands):
                return True
    return False


def _segments(command):
    return [seg for seg in re.split(r"\|\||&&|[;|&\n]", command) if seg.strip()]


_PY_RUN_OPTIONS = ("-I", "-B", "-s", "-S", "-u")  # the only options a run of a listed Python file may carry: none can name code, a module or another file to run. -E is left out: bash_writes_guarded reads it as another interpreter's inline-code option, and -I already covers it


def _runs_listed_python(words, seg):
    """True for `python [options] <file> [the file's own arguments]`, where <file> is redact_view.py, the module or the cleanup command and every word before it is one of
    _PY_RUN_OPTIONS written alone. Anything else before the file is refused, whether or not it is joined to a value: an option that names code or a module to run, a lone
    dash, an interactive run, a bundle of option letters. With one of those Python runs something other than the file, and the file would only be an argument handed to
    it. The cleanup command takes no path, so nothing may follow it but --apply.
    seg is the raw text of the part of the command the words came from. The path the rule compares must be the path the machine opens, so the file's word must be:
    written plainly, with no quote or backslash and no white space of any kind before, in or after it (the shell would hand Python other text than the rule read,
    and a white-space character on the end of the word is part of the file name the shell passes on);
    a full path from /, ~/ or $HOME/ (a relative path is opened against the working folder, not against /), with no other variable (the shell's value may differ from the
    hook's) and no ".." (it is removed as text here, but the shell follows links first); for $HOME/, a home with no white space or wildcard (the shell would split it);
    the listed file's own name, never a link to it (a link can be re-pointed between the check and the run, and its name can be a wildcard pattern)."""
    i = 1
    while i < len(words) and words[i] in _PY_RUN_OPTIONS:
        i += 1
    if i >= len(words) or words[i].startswith("-"):
        return False
    word = words[i]
    if any(ch.isspace() for ch in word):
        return False
    if re.split(r"[ \t]+", seg.strip(" \t"))[:i + 1] != words[:i + 1]:
        return False
    if not word.startswith(("/", "~/", "$HOME/")) or "$" in word[1:] or ".." in word.split("/"):
        return False
    if word.startswith("$HOME/") and re.search(r"[\s*?\[]", home()):
        return False
    p = expand(word, "/")
    cleanup = os.path.join(wall_dir(), "wall_cleanup.py")
    if p in (cleanup, os.path.realpath(cleanup)):
        return all(a == "--apply" for a in words[i + 1:])
    listed = (os.path.join(home(), ".claude", "scripts", "redact_view.py"), os.path.join(wall_dir(), "no_ai_wall.py"))
    return p in listed or p in tuple(os.path.realpath(f) for f in listed)  # run, not edited


def _read_only_listing(command):
    """True for a command that only looks at a file's existence or size, or reads the list through redact_view.py, or runs the module's own check commands, or runs the wall's cleanup
    command wall_cleanup.py (which takes no path and moves only strays out of the wall's folder), with no redirection, no command substitution and no program outside the
    short list. A Python file on that list is there to be run as itself and nothing else: _runs_listed_python holds the rule. This is the only gate for such a command
    (bash_writes_guarded does not look inside a Python run), so it has to refuse every form in which Python would run something other than the listed file.
    The program is the first word of each part of the command, so a part that starts by setting a variable is refused: that word is not a program, and
    the real one after it would go unread. For the same reason the whole command is refused when it holds a parenthesis or a brace: those group commands and
    define functions, so they start a command where _segments sees no new part (`ls() ( python -m x file ); ls` gives the name ls a new body), and the
    program inside would go unread too. A first word must be a bare program name with no slash, or that name directly under /usr/bin or /bin: a path to any other folder is refused, because a program there can carry a trusted name, and so is a tilde, which the shell expands."""
    if re.search(r"[<>`(){}]", command):
        return False
    for seg in _segments(command):
        words = _words(seg)
        if not words:
            continue
        folder, prog = os.path.split(words[0])
        if "=" in words[0] or not re.fullmatch(r"[A-Za-z0-9_.+-]+", prog) or folder not in ("", "/usr/bin", "/bin"):
            return False  # a word that sets a variable is not the program: `A=/ls python ...` must not be read as ls; nor is a word the shell expands (a tilde); nor is a path to any folder but the two system ones, where a program can carry a trusted name
        if prog in _READ_ONLY_PROGRAMS:
            continue
        if re.fullmatch(r"python[\d.]*", prog) and _runs_listed_python(words, seg):
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


_SUBAGENT_TOOLS = ("Agent", "Task")


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
        return (tool, "protected file", "no-ai wall: the no-AI list, the wall's own folder (~/.claude/no-ai-wall) and the settings files cannot be changed from a session. Edit them outside the model.")

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
    # in them is read as a path or as text that names one. A subagent prompt (Agent, Task) is judged by what the subagent does, not by
    # the words in it: its own tool calls pass through this same hook, so a prompt that only mentions a protected file is not refused.
    # It still may not name a listed path (that text goes to the vendor model).
    is_subagent = tool in _SUBAGENT_TOOLS
    for text in _strings(ti):
        pieces = [text] + [t.strip("'\"`,;:()[]{}<>") for t in text[:20000].split()[:400]]  # a sentence naming a relative path; capped
        for piece in pieces:
            if not _path_shaped(piece):
                continue
            h = paths.match(piece, cwd)  # a relative one is joined with the session folder, as the shell tools do
            if h:
                return deny_path(h)
            if not is_subagent and is_guarded(piece):
                return deny_protected()
        h = text_names_listed_path(text, paths)
        if h:
            return deny_path(h)
        if not is_subagent and any(is_guarded(t) for t in text_paths(text)):
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
