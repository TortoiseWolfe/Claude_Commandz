#!/usr/bin/env python3
"""The no-AI wall: one private list of paths and one of mail senders that no model session may touch.

Some clients require that their information never reaches a proprietary AI provider. The lists are private config and
never live in a repo:

  ~/.config/afa/no-ai-paths.txt     one path or glob per line. `*` stays inside one path segment, `**` crosses segments,
                                    `?` is one character, `~/` is the home folder. A match covers everything under it.
                                    Blank lines and lines starting with # are ignored.
  ~/.config/afa/no-ai-senders.tsv   client, repo_dir, kind, gmail_from (the columns of hub/client-senders.tsv). A
                                    gmail_from with an @ is one exact address; a bare domain covers it and its subdomains.

A missing list means the wall is off for that list (one warning line per run, from warn_missing). A list that exists
but cannot be read or parsed fails closed: every path and every sender then counts as listed. Symlinks are resolved
before matching, and the lexical path is matched too, so neither form gets around the wall.

Everything here is a pure function of the two files, so the lane launcher, the tool hook, the mail poller and the digest
all decide the same way. Command line (used by the bash helper no_ai_wall.sh):

  no_ai_wall.py status                       counts only
  no_ai_wall.py check-path [--cwd D] P...    exit 0 = none listed, 3 = one is listed (or a list is unreadable)
  no_ai_wall.py check-text FILE              exit 3 when the text names a listed path
  no_ai_wall.py check-sender ADDRESS         prints the client and exits 3 when the sender is listed
  no_ai_wall.py senders                      prints each listed gmail_from (for a Gmail query); exit 3 when the list is unreadable
Environment overrides (tests): NO_AI_PATHS_FILE, NO_AI_SENDERS_FILE, NO_AI_REFUSAL_LOG.
"""
import datetime
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


def senders_file():
    return os.environ.get("NO_AI_SENDERS_FILE") or os.path.join(home(), ".config", "afa", "no-ai-senders.tsv")


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


def _pattern_regex(pattern):
    segs = [s for s in pattern.split("/") if s != ""]
    body = "/".join(_segment_regex(s) for s in segs)
    flags = re.DOTALL | (re.IGNORECASE if pattern.lower().startswith("/mnt/") else 0)
    return re.compile(r"/" + body + r"(?:/.*)?\Z", flags)


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
        self.segre = [[re.compile(r, re.DOTALL) for r in sv] for sv in self.segs]

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


class SenderList:
    def __init__(self, rows=(), status="ok", reason=""):
        self.rows = list(rows)  # (client, repo_dir, kind, address-or-domain), all but client lowercased
        self.status = status
        self.reason = reason

    @property
    def on(self):
        return self.status != "missing"

    def match(self, address):
        """The client name for a listed sender, UNREADABLE when the list is unreadable, else None."""
        if self.status == "bad":
            return UNREADABLE
        a = re.sub(r"\s+", "", (address or "")).lower()
        m = re.search(r"<([^<>]+)>", a)
        if m:
            a = m.group(1)
        if not a:
            return None
        for client, _repo, _kind, pat in self.rows:
            if "@" in pat:
                if a == pat:
                    return client
            elif a.endswith("@" + pat) or a.endswith("." + pat):
                return client
        return None

    @property
    def clients(self):
        return sorted({r[0] for r in self.rows}, key=str.lower)

    def mentions(self, text):
        """True when text names a listed client (case-insensitive) or a listed address. An unreadable list mentions everything."""
        if self.status == "bad":
            return True
        low = (text or "").lower()
        for client, _repo, _kind, pat in self.rows:
            if len(client) >= 3 and client.lower() in low:
                return True
            if "@" in pat and pat in low:
                return True
        return False


def load_senders(path=None):
    path = path or senders_file()
    text, st = _read(path)
    if st != "ok":
        return SenderList(status=st, reason="file is %s" % st)
    rows = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if cells[0].lower() == "client" and len(cells) >= 4 and cells[3].lower() == "gmail_from":
            continue
        if len(cells) < 4 or not cells[0] or not cells[3] or re.search(r"\s", cells[3]):
            return SenderList(status="bad", reason="row %d has no client or no gmail_from" % n)
        rows.append((cells[0], cells[1], cells[2], cells[3].lower()))
    return SenderList(rows)


def wall_on():
    """True when at least one list exists (a missing pair means the wall is off everywhere)."""
    return load_paths().on or load_senders().on


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


def _literal_prefix(pattern):
    """The folder a glob pattern starts from: its leading segments with no wildcard."""
    segs = pattern.split("/")
    lit = []
    for s in segs:
        if any(ch in GLOB_CHARS for ch in s):
            break
        lit.append(s)
    return "/".join(lit) or "/"


def evaluate(payload, paths=None):
    """The hook's decision for one tool call. -> None to allow, else (tool, entry, message) to deny."""
    paths = paths or load_paths()
    if not paths.entries and paths.status != "bad":
        return None
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool not in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep", "Glob", "Bash"):
        return None

    if paths.status == "bad":
        lists = (paths_file(), senders_file())
        target = ti.get("file_path") or ti.get("notebook_path") or ""
        if tool in ("Read", "Edit", "Write") and target and os.path.realpath(expand(target, cwd)) in [os.path.realpath(x) for x in lists]:
            return None  # the one way to repair the list from inside a session
        return (tool, UNREADABLE, "no-ai wall: the no-AI path list exists but cannot be read (%s), so every file and shell tool is refused until it is fixed. Edit %s outside the model, or open that one file with Read, Edit or Write." % (paths.reason, paths_file()))

    hit = paths.match(cwd, "/")
    if hit:
        return (tool, hit, "no-ai wall: this session's working folder is on the no-AI list. Start a clean room instead: lane.sh --clean-room <scratch-folder> ...")

    def deny_path(entry):
        return (tool, entry, "no-ai wall: that path is on the no-AI list and may not be opened, searched or changed in this session. If the task needs it, work from a clean room (lane.sh --clean-room) and leave that folder out of the prompt.")

    def deny_root(entry):
        return (tool, entry, "no-ai wall: this search would walk into a folder on the no-AI list (%s). Narrow the search root so it leaves that folder out." % entry)

    if tool in ("Read", "Edit", "Write", "MultiEdit"):
        p = ti.get("file_path")
        if p:
            h = paths.match(str(p), cwd)
            if h:
                return deny_path(h)
        return None
    if tool == "NotebookEdit":
        p = ti.get("notebook_path")
        if p:
            h = paths.match(str(p), cwd)
            if h:
                return deny_path(h)
        return None
    if tool in ("Grep", "Glob"):
        root = str(ti.get("path") or cwd)
        h = paths.match(root, cwd)
        if h:
            return deny_path(h)
        h = paths.contains(root, cwd)
        if h:
            return deny_root(h)
        if tool == "Glob":
            pat = str(ti.get("pattern") or "")
            if pat.startswith(("/", "~", "$HOME")):
                pre = _literal_prefix(expand(pat, cwd))
                h = paths.match(pre, cwd)
                if h:
                    return deny_path(h)
                h = paths.contains(pre, cwd)
                if h:
                    return deny_root(h)
        return None
    if tool == "Bash":
        r = bash_hits(str(ti.get("command") or ""), cwd, paths)
        if r:
            return deny_root(r[0]) if r[1] == "root" else deny_path(r[0])
    return None


# ---- command line ----------------------------------------------------------------------------------------------------

def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    pl, sl = load_paths(), load_senders()
    if cmd == "status":
        for which, lst, n in (("paths", pl, len(pl.entries)), ("senders", sl, len(sl.rows))):
            print("%s: %s%s" % (which, lst.status, ", %d entries" % n if lst.status == "ok" else (" (%s)" % lst.reason if lst.reason else "")))
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
    if cmd == "check-sender":
        if not sl.on:
            warn_missing("senders", senders_file())
            return 0
        hit = sl.match(rest[0] if rest else "")
        if hit:
            print(hit)
            return 3
        return 0
    if cmd == "senders":
        if not sl.on:
            warn_missing("senders", senders_file())
            return 0
        if sl.status == "bad":
            sys.stderr.write("no-ai wall: the sender list is unreadable (%s)\n" % sl.reason)
            return 3
        for pat in sorted({r[3] for r in sl.rows}):
            print(pat)
        return 0
    sys.stderr.write("unknown command: %s\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
