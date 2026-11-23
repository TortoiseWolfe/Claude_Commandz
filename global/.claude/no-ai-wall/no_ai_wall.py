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

Where it lives: ~/.claude/no-ai-wall/ holds only the wall's own files (this module, no_ai_wall.sh, no-ai-wall-guard.py). The tamper
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
_WALL_FILES = ("no-ai-wall-guard.py", "no_ai_wall.py", "no_ai_wall.sh")


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


# ---- reading a shell command as commands (issue 130) -----------------------------------------------------------------
# The old reading took every word of the whole text (heredoc prose, quoted sentences, words on both sides of a pipe) as one command line, so
# a word like find, du or tar anywhere made the command "a search", and a search with no root named always walked the working folder.
# The reading below keeps the old, wider one whenever the text cannot be read with certainty (an unclosed quote).

_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "mksh", "fish", "csh", "tcsh"}
_DOC_EXTENSIONS = (".md", ".txt", ".json", ".yml", ".yaml", ".tsv", ".csv", ".log", ".html", ".diff", ".patch")  # a file with one of these is text, not a script
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_][A-Za-z0-9_]*))")
_HEREDOC_READERS = {"cat", "tee", "node", "perl", "ruby", "php", "deno", "bun", "gh", "git", "sed", "awk", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "jq", "grep"}
_HEREDOC_RUNNERS = {"node", "perl", "ruby", "php", "deno", "bun", "awk", "sed"}
_SHELL_AFTER_PIPE = re.compile(r"(?<![\w.-])(?:bash|sh|zsh|dash|ksh|eval|source|xargs|ssh|su|sudo|env|exec|python[\d.]*|node|perl|ruby|php)(?![\w.-])")


def _shell_commands(text, written=None):
    """-> a list of commands, each a list of (word, quoted), or None when the text cannot be read with certainty. A command ends at an unquoted
    ; & | newline, parenthesis, backtick or `$(`. A redirect and its target are dropped (the targets of output redirects are added to the list
    `written` when one is given). A quoted word is data, except the script of a shell's -c."""
    cmds, toks = [], []
    buf, quoted, started, skip, skip_out = [], False, False, False, False
    i, n = 0, len(text)

    def end_word():
        nonlocal buf, quoted, started, skip
        if started:
            if skip:
                skip = False
                if skip_out and written is not None:
                    written.append("".join(buf))
            else:
                toks.append(("".join(buf), quoted))
        buf, quoted, started = [], False, False

    def end_cmd():
        nonlocal toks, skip
        end_word()
        skip = False
        if toks:
            cmds.append(toks)
        toks = []

    while i < n:
        c = text[i]
        if c == "\\":
            if text[i + 1:i + 2] != "\n":
                buf.append(text[i + 1:i + 2])
                started = quoted = True
            i += 2
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            buf.append(text[i + 1:j])
            started = quoted = True
            i = j + 1
        elif c == '"':
            j, piece = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    j += 1
                piece.append(text[j])
                j += 1
            if j >= n or "`" in piece or "$(" in "".join(piece):  # a substitution inside quotes runs: unsure
                return None
            buf.append("".join(piece))
            started = quoted = True
            i = j + 1
        elif c in " \t":
            end_word()
            i += 1
        elif c == "#" and not started:
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in ";\n|`()" or (c == "&" and text[i + 1:i + 2] != ">"):
            end_cmd()
            i += 1
        elif c == "$" and text[i + 1:i + 2] == "(":
            end_cmd()
            i += 2
        elif c in "<>" or (c == "&" and text[i + 1:i + 2] == ">"):
            if started and not quoted and "".join(buf).isdigit():
                buf, started = [], False  # the fd number of `2>&1`
            end_word()
            skip_out = c != "<"
            while i < n and text[i] in "<>&|":
                i += 1
            skip = True
        else:
            buf.append(c)
            started = True
            i += 1
    end_cmd()
    return cmds


def _heredoc_is_data(before, after):
    """True when the body of a heredoc is text that no shell reads: the receiver (the command before `<<`) is a program that only reads it
    (cat, tee, git, gh, sed ...), it is not piped on into a shell or interpreter, and it is not written to a file that could be a script."""
    cmds = _shell_commands(before)
    if not cmds or _SHELL_AFTER_PIPE.search(after):
        return False
    words = [w for w, q in cmds[-1]]
    i = _skip_wrappers(words, scan=False)
    while i < len(words) and (words[i].startswith("-") or words[i].isdigit()):
        i += 1
    if i >= len(words):
        return False
    prog = os.path.basename(words[i])
    if not (prog in _HEREDOC_READERS or re.fullmatch(r"python[\d.]*", prog)):
        return False
    targets = [m.group(1).strip("'\"") for m in _REDIRECT.finditer(before)]
    if prog == "tee":
        targets += [w for w in words[i + 1:] if not w.startswith("-")]
    if all(t.endswith(_DOC_EXTENSIONS) or t == "/dev/null" or t.startswith("&") for t in targets):
        return prog
    return None


def _heredoc_runs_it(prog):
    """True for a receiver that RUNS its heredoc (an interpreter, awk, sed): the body is data to no shell, but it can start a search, so the
    roots it names are still checked (senior review 1 of the issue 130 fix)."""
    return prog in _HEREDOC_RUNNERS or re.fullmatch(r"python[\d.]*", prog) is not None


def _strip_heredocs(command, runner_bodies=None, receivers=None):
    """The command without the bodies of heredocs whose text no shell reads (see _heredoc_is_data). A heredoc with no terminator line, or
    one handed to a shell (`bash <<EOF`, `cat <<EOF | sh`), keeps its body: it is judged as the commands it is. The bodies of heredocs an
    interpreter runs are dropped too but added to `runner_bodies` when one is given, so the caller can still read them for the roots they name."""
    if "<<" not in command:
        return command
    lines = command.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC.search(line)
        if not m:
            continue
        end = next(g for g in m.groups()[1:] if g is not None)
        j = i
        while j < len(lines) and (lines[j].lstrip("\t") if m.group(1) else lines[j]) != end:
            j += 1
        if j >= len(lines):
            continue
        receiver = _heredoc_is_data(line[:m.start()], line[m.end():])
        if receiver:
            if runner_bodies is not None and _heredoc_runs_it(receiver):
                runner_bodies.append("\n".join(lines[i:j]))
                if receivers is not None:
                    holder = _shell_commands(line[:m.start()]) or [[]]
                    prog, rest = _program_of(holder[-1])
                    if prog == receiver and not _has_own_program(prog, rest):
                        # credit only an interpreter that really reads this heredoc as its program (a `python3 a.py <<EOF` runs a.py, so a later
                        # pipe-fed python gets none of it)
                        receivers[receiver] = receivers.get(receiver, 0) + 1
            i = j + 1
        else:
            out.extend(lines[i:j + 1])
            i = j + 1
    return "\n".join(out)


def _split_statements(text):
    """-> [(statement text, separator)] split at an unquoted ; newline && || | & outside parentheses (quotes and backslashes respected)."""
    out, start, depth, i, n = [], 0, 0, 0, len(text)
    sq = dq = False
    while i < n:
        c = text[i]
        if c == "\\" and not sq:
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif c == '"' and not sq:
            dq = not dq
        elif not sq and not dq:
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and c in ";\n|&":
                if c == "&" and (text[i - 1:i] in (">", "<") or text[i + 1:i + 2] == ">"):
                    i += 1
                    continue
                sep = c
                if c in "|&" and text[i + 1:i + 2] == c:
                    sep = c * 2
                out.append((text[start:i], sep))
                i += len(sep)
                start = i
                continue
        i += 1
    out.append((text[start:], ""))
    return out


_ASSIGN = re.compile(r"[ \t]*([A-Za-z_][A-Za-z0-9_]*)=((?:[A-Za-z0-9_./:@%+=,~-]|\$HOME|\$\{HOME\})*)[ \t]*\Z")
_BLOCK_START = re.compile(r"[ \t]*(?:if|then|else|elif|fi|for|while|until|do|done|case|esac|select|function|!|\{|\})(?![\w-])")
# a statement that can bind a variable without naming it, or through a name held in another: `. file`, source, eval, read, mapfile, declare -n,
# printf -v, getopts, let, a process substitution. The first one ends the trust in every value _resolve_vars knows.
_UNSEEN_REBIND = re.compile(r"(?:^|[\s;&|({`])[\\'\"]*(?:\.|source|eval|read|mapfile|readarray|getopts|let|declare|typeset|local|export|readonly|unset"
                            r"|trap|alias|shopt|enable|function)[\\'\"]*(?=[\s;&|)}`]|$)"
                            r"|\bprintf\b[^;&|\n]*?\s['\"]?-[A-Za-z]*v|\bwait\b[^;&|\n]*?\s-[A-Za-z]*p"  # printf -v NAME and printf -vNAME, wait -p NAME
                            r"|[<>]\(|\$\(\(|\(\(")  # (a builtin's name quoted or backslashed is still the builtin: 'read' W, \eval x)


# Programs that are plain files on disk: a child process can never change the parent shell's variables. Any other first word of a statement may be a
# function, an alias or a builtin that runs a trap, and so can rebind a variable that no assignment names, so it ends the trust (issue 130, review 2).
_CHILD_ONLY = {"cd", "pushd", "popd", "echo", "printf", "cat", "ls", "pwd", "true", "false", ":", "head", "tail", "wc", "sort", "uniq", "grep", "egrep",
               "fgrep", "rg", "find", "fd", "cp", "mv", "rm", "mkdir", "rmdir", "touch", "chmod", "chown", "ln", "tee", "sed", "awk", "diff", "stat", "du",
               "tar", "zip", "unzip", "gzip", "rsync", "curl", "wget", "git", "gh", "docker", "node", "bash", "sh", "timeout", "sleep", "date", "jq", "tr",
               "cut", "basename", "dirname", "readlink", "realpath", "mktemp", "perl", "ruby", "php", "xargs", "env", "sudo", "gitleaks"}
_LEADING_ASSIGN = re.compile(r"[ \t]*[A-Za-z_][A-Za-z0-9_]*=\S*[ \t]+")


def _first_word_is_child_only(text):
    t = text
    while _LEADING_ASSIGN.match(t):
        t = t[_LEADING_ASSIGN.match(t).end():]
    m = re.match(r"[ \t]*[\\'\"]*([^\s;&|()<>\\'\"]+)", t)
    if not m:
        return False
    word = m.group(1).rsplit("/", 1)[-1]
    return word in _CHILD_ONLY or re.fullmatch(r"python[\d.]*", word) is not None


def _subst(text, known):
    """`$NAME` and `${NAME}` replaced by their known value, outside single quotes."""
    if not known or "$" not in text:
        return text
    out, i, n, sq, dq = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if c == "\\" and not sq:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == "'" and not dq:
            sq = not sq
        elif c == '"' and not sq:
            dq = not dq
        elif c == "$" and not sq:
            m = re.match(r"\$(?:\{(\w+)\}|(\w+))", text[i:])
            name = (m.group(1) or m.group(2)) if m else None
            if name in known:
                out.append(known[name])
                i += m.end()
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _resolve_vars(command):
    """The command with `$NAME` written out for every plain assignment (`W=/abs/dir; cd $W`) that runs unconditionally before the use: the
    statements up to the first one that sits behind && || | & or inside a block, subshell or heredoc. The text a later check reads then names
    the folder the shell will really use. A name that is reassigned later in any other way, or set from anything but plain text, is dropped
    (it stays as `$NAME`, which the write check still fails closed on)."""
    if "$" not in command or "=" not in command:
        return command
    known, out, collecting = {}, [], True
    heredoc = _HEREDOC.search(command)
    cut = command.rfind("\n", 0, heredoc.start()) + 1 if heredoc else len(command)  # no statement from the heredoc's line on is a plain assignment
    head, tail = command[:cut], command[cut:]
    for text, sep in _split_statements(head):
        s = _subst(text, known)
        m = _ASSIGN.match(s) if collecting and _ASSIGN.match(text) else None
        if m:
            known[m.group(1)] = m.group(2)
        else:
            if _UNSEEN_REBIND.search(text) or not _first_word_is_child_only(text) or re.search(r"[(){}`]", text):
                # a sourced file, eval, read, `<(`, a trap, an alias, or a call to something that is no plain program (a function) can set any
                # variable without naming it: nothing known is trusted past here
                known.clear()
                collecting = False
            for name in list(known):
                # keep a value only when every later mention of the name is a plain read ($NAME or ${NAME}); NAME+=, NAME[0]=, ((NAME=..)),
                # ${NAME:=..}, declare -n, read, export and every other form leave a bare mention behind and drop it
                if re.search(r"(?<![\w$])%s(?!\w)" % name, re.sub(r"\$\{%s\}|\$%s(?!\w)" % (name, name), " ", text)):
                    del known[name]
        if sep in ("&&", "||", "|", "&") or _BLOCK_START.match(text) or re.search(r"[(){}`]", text):
            collecting = False
        out.append(s + sep)
    out.append(tail)  # as written: a heredoc's child shell never sees a variable the parent did not export
    return "".join(out)


# options that take a value, so the word after one is not an operand (a search root or a pattern)
_VALUE_SHORT = {"grep": "ABCmefdD", "rg": "gtTABCmjMEfedr", "fd": "eEtdSczjxXo", "tree": "LPIo", "du": "dBtX", "ls": "IwT"}
_VALUE_LONG = {
    "grep": {"--after-context", "--before-context", "--context", "--max-count", "--regexp", "--file", "--directories", "--devices", "--include", "--exclude",
             "--exclude-dir", "--include-dir", "--label", "--binary-files"},
    "rg": {"--glob", "--iglob", "--type", "--type-not", "--type-add", "--after-context", "--before-context", "--context", "--max-count", "--threads",
           "--max-columns", "--encoding", "--file", "--regexp", "--max-depth", "--maxdepth", "--max-filesize", "--sort", "--sortr", "--pre", "--pre-glob",
           "--replace", "--colors", "--path-separator", "--context-separator", "--ignore-file", "--engine"},
    "fd": {"--extension", "--exclude", "--type", "--max-depth", "--min-depth", "--exact-depth", "--size", "--color", "--threads", "--owner", "--changed-within",
           "--changed-before", "--ignore-file", "--format", "--max-results", "--batch-size", "--path-separator"},
    "tree": {"--charset", "--filelimit", "--sort", "--timefmt"},
    "du": {"--max-depth", "--block-size", "--threshold", "--exclude-from", "--exclude", "--files0-from", "--time-style"},
    "ls": {"--ignore", "--width", "--tabsize", "--sort", "--time", "--format", "--color", "--block-size"},
}
_PATTERN_OPTIONS = ("-e", "-f", "--regexp", "--file", "--files", "-g")  # a pattern given by option: every operand is then a path
_SEARCH_KEY = {"grep": "grep", "egrep": "grep", "fgrep": "grep", "zgrep": "grep", "rg": "rg", "ag": "rg", "ack": "rg", "fd": "fd", "fdfind": "fd"}
_ROOTS_ONLY = {"rsync", "tar", "zip", "locate", "mlocate", "plocate", "scp"}  # name their own folders; with none named they walk nothing


def _operands(key, rest):
    """The non-option words of a command line, past each option's value."""
    out, i = [], 0
    while i < len(rest):
        w = rest[i]
        i += 1
        if w == "--":
            out.extend(rest[i:])
            break
        if w.startswith("--"):
            if "=" not in w and w in _VALUE_LONG.get(key, ()):
                i += 1
        elif w.startswith("-") and len(w) > 1:
            if w[-1] in _VALUE_SHORT.get(key, ""):
                i += 1
        else:
            out.append(w)
    return out


def _names_a_root(words, cur):
    """True when one of the operands plainly names a place to search: a path, a wildcard, or something that exists."""
    for w in words:
        if "/" in w or w.startswith((".", "~", "$")) or any(ch in GLOB_CHARS for ch in w) or os.path.lexists(expand(w, cur)):
            return True
    return False


def _search_of(prog, rest, cur, xargs):
    """-> (recursive, walks_working_folder) for one program with its words."""
    if prog in _ROOTS_ONLY:
        return True, False
    if prog in ("cp", "mv"):
        return any(x.startswith("-") and not x.startswith("--") and ("r" in x or "R" in x or "a" in x) for x in rest), False
    if prog == "find":
        roots = []
        for x in rest:
            if x in ("-H", "-L", "-P") or x.startswith(("-O", "-D")):
                continue
            if x.startswith(("-", "(", "!", ",")):
                break
            roots.append(x)
        return True, not (xargs or _names_a_root(roots, cur))
    if prog in ("tree", "du") or (prog == "ls" and any((x.startswith("-") and not x.startswith("--") and "R" in x) or x == "--recursive" for x in rest)):
        return True, not (xargs or _names_a_root(_operands(prog, rest), cur))
    key = _SEARCH_KEY.get(prog)
    if key == "grep" and not any((x.startswith("-") and not x.startswith("--") and ("r" in x or "R" in x)) or x in ("--recursive", "--dereference-recursive") for x in rest):
        return False, False
    if key is None and not (prog == "git" and "grep" in rest[:3]):
        return False, False
    if prog == "git":
        rest = rest[rest.index("grep") + 1:]
        key = "grep"
    ops = _operands(key, rest)
    if key == "fd" and any(x in ("--search-path", "--base-directory") or x.startswith(("--search-path=", "--base-directory=")) for x in rest):
        return True, False
    if not any(x.startswith(_PATTERN_OPTIONS) for x in rest):
        ops = ops[1:]  # the first operand is the pattern
    return True, not (xargs or _names_a_root(ops, cur))


def _is_launcher_words(words):
    i = _skip_wrappers(words, scan=False)
    if i < len(words) and os.path.basename(words[i]) in ("bash", "sh"):
        i += 1
        if i < len(words) and words[i].startswith("-"):
            return False
    return i < len(words) and os.path.basename(words[i]) == "lane.sh"


_RUNS_A_STRING = ("env", "ssh", "su", "watch", "script", "parallel", "flock", "runuser", "sg")
_RUNS_A_PROGRAM = ("docker", "podman", "kubectl", "nsenter", "chroot", "exec", "xargs", "sudo", "nohup", "nice", "timeout", "setsid")  # a program word follows


# programs that only read or move the files they are given: when the text writes a file, any OTHER program in it could be running that file
# (an interpreter, make, busybox, a path no list knows), so the text is read widely unless every program is one of these or a writer
_DATA_TOOLS = {"cat", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp", "sort", "uniq", "cut", "tr", "rm", "mkdir", "touch", "git", "gh", "echo",
               "printf", "cd", "true", "false", "test", "[", "grep", "egrep", "fgrep", "rg", "date", "sleep", "pwd", "basename", "dirname", "realpath",
               "readlink", "jq", "sha256sum", "md5sum", "du", "df", "which", "tee", "cp", "mv", "install", "ln", "dd", "curl", "wget", "tar", "unzip", "rsync"}
_WRITERS = {"tee", "cp", "mv", "install", "ln", "dd", "curl", "wget", "tar", "unzip", "rsync"}
# what an interpreter runs from its own string, pipe or file: the roots a search inside it names count, as for a heredoc
_INLINE_RUNNERS = {"node", "perl", "ruby", "php", "deno", "bun", "awk", "sed", "gawk", "mawk", "nawk", "lua", "tclsh", "pwsh"}
_STDIN_RUNNERS = {"node", "perl", "ruby", "php", "deno", "bun", "lua", "tclsh", "pwsh"}  # (awk and sed are given their program as an operand)
_INLINE_FLAG = re.compile(r"-[A-Za-z]*[ceErCS]")
_SCRIPT_FILE = re.compile(r"\.(?:py|pyw|js|mjs|cjs|ts|pl|rb|php|lua|tcl|ps1)\Z")
# a data tool is no data tool when an option or an alias hands it a program to run (git -c core.pager=..., tar --to-command, sort --compress-program)
_CAN_RUN = {"git", "gh", "tar", "rsync", "sort", "curl", "wget", "dd", "install", "ln"}
_RUNS_CODE_ARG = re.compile(r"^-e|^-[A-Za-z]*I$|^--(?:config|exec|extcmd|ext-diff|textconv|upload-pack|receive-pack|to-command|use-compress-program|"
                            r"compress-program|checkpoint-action|info-script|new-volume-script|rsh|rsync-path|proxy-command|ssh-command|pager|editor)|^!|=!|"
                            r"^(?:alias|extension|exec|foreach|run|config)$")  # config: git config core.pager/sshCommand/editor name a program to run later
_GIT_CONFIG_ARG = re.compile(r"^-c$|^-c\S*=")  # git -c name=value sets a config value that can name a program (core.pager, alias.x=!...); tar -c is just create


def _can_run_code(p, rest, env=False):
    """True for a data tool that is handed a program to run: an option, subcommand or alias (see _RUNS_CODE_ARG), or a variable set just for it
    (GIT_PAGER=..., GIT_SSH_COMMAND=..., PAGER=...)."""
    return p in _CAN_RUN and (env or any(_RUNS_CODE_ARG.search(a) for a in rest) or (p == "git" and any(_GIT_CONFIG_ARG.search(a) for a in rest)))


def _sets_a_variable_for(cmd):
    """True when a command's words before its program include a NAME=value (`GIT_PAGER=less git log`, `env PAGER=x gh pr view`)."""
    ws = [w for w, q in cmd]
    return any(re.match(r"[A-Za-z_]\w*=", w) for w in ws[:_skip_wrappers(ws, scan=False)])


def _has_own_program(prog, rest):
    """True only when an interpreter's words plainly hold its own program: an inline flag (-c, -e, -E, -m for python) or an operand that is a script
    file by its name. An option's value, an unknown option or a bare word is none of these (fail closed: no table of every interpreter's options)."""
    return any(_INLINE_FLAG.fullmatch(x) or (x == "-m" and prog.startswith("python")) or (not x.startswith("-") and _SCRIPT_FILE.search(x)) for x in rest)


def _program_of(cmd):
    """-> (the program's base name or None, the words after it) of a command, wrappers skipped."""
    ws = [w for w, q in cmd]
    i = _skip_wrappers(ws, scan=False)
    return (os.path.basename(ws[i]), ws[i + 1:]) if i < len(ws) else (None, [])


def _searches(text, cwd, depth=0, bodies=None, heredocs=None):
    """-> (recursive, folders, words) for a shell text, or None when it cannot be read with certainty. recursive: some command in it searches
    or copies a folder tree. folders: the working folders of the searches that name no root, which therefore walk their working folder.
    words: every word of the commands (a lane launcher's own words left out) for the checks that read operands."""
    written = []
    cmds = _shell_commands(text, written)
    if cmds is None or depth > 4:
        return None
    if any(not (t.endswith(_DOC_EXTENSIONS) or t == "/dev/null" or t.isdigit() or t == "-") for t in written):
        return None  # text written to a file that could be a script may be run later in the text: the wide reading
    live = [c for c in cmds if not _is_launcher_words([w for w, q in c])]
    progs = [_program_of(c) + (_sets_a_variable_for(c),) for c in live]
    if (any(t not in ("/dev/null", "-") and not t.isdigit() for t in written) or any(p in _WRITERS for p, rest, env in progs)) and any(
            p is not None and (p not in _DATA_TOOLS or _can_run_code(p, rest, env)) for p, rest, env in progs):
        return None  # something is written (a redirect, tee, cp...) and some other program is in the text: it may be running that file, whatever its name ends in
    recursive, folders, words, cur = False, [], [], cwd
    if heredocs is None:
        heredocs = {}  # {program: heredocs _strip_heredocs dropped for it}; none given means none were recognised
    for cmd in cmds:
        ws = [w for w, q in cmd]
        if _is_launcher_words(ws):
            continue
        words.extend(ws)
        if ws[0] == "cd" and not cmd[0][1]:
            arg = next((a for a in ws[1:] if not a.startswith("-")), None)
            cur = cwd if arg is None or "$" in arg or "`" in arg else expand(arg, cur)
            continue
        if os.path.basename(ws[0]) in ("echo", "printf") and not cmd[0][1]:
            continue
        xargs = any(os.path.basename(w) == "xargs" for w, q in cmd if not q)
        prog_at = _skip_wrappers(ws, scan=False)
        for i, (w, q) in enumerate(cmd):
            behind_wrapper = not re.search(r"\s", w) and _known_program(w) and any(os.path.basename(p) in _WRAPPERS for p, pq in cmd[:i] if not pq)
            if q and i != prog_at and i != 0 and not behind_wrapper:
                # a quoted word is data, except the one a command that runs a string (env -S, ssh, watch...) hands on to a shell. The word the
                # command runs is never data, however it is quoted or escaped: 'find' and \find are find.
                if re.search(r"\s", w) and any(os.path.basename(p) in _RUNS_A_STRING for p, pq in cmd[:i] if not pq):
                    sub = _searches(w, cur, depth + 1, bodies)
                    if sub is None:
                        return None
                    recursive, folders, words = recursive or sub[0], folders + sub[1], words + sub[2]
                continue
            prog = os.path.basename(w)
            rest = ws[i + 1:]
            script = None
            if prog in _SHELLS:
                for k, x in enumerate(rest):
                    if x.startswith("-") and not x.startswith("--") and "c" in x:
                        script = rest[k + 1] if k + 1 < len(rest) else None
                        break
            elif prog == "eval":
                script = " ".join(rest)
            runs_here = i == prog_at or any(os.path.basename(p) in _RUNS_A_STRING + _RUNS_A_PROGRAM + _WRAPPERS for p, pq in cmd[:i] if not pq)
            if runs_here and (prog in _SHELLS and (script is None or xargs) or prog in ("source", ".") or (prog == "tee" and any(
                    not (a.startswith("-") or a.endswith(_DOC_EXTENSIONS) or a == "/dev/null") for a in rest))):
                return None  # a shell fed a file or a pipe, or a file a later command could run: what it reads is out of sight, so the wide reading
            if runs_here and (prog in _INLINE_RUNNERS or re.fullmatch(r"python[\d.]*", prog)):
                # an interpreter runs its string, pipe or file, and that code can start a search: every quoted word it was given is read for the roots
                # it names; one with no program of its own (fed by a pipe or a redirect, which is out of sight) takes the wide reading
                if bodies is not None:
                    bodies.extend(x for x, xq in cmd[i + 1:] if xq)
                if (prog in _STDIN_RUNNERS or prog.startswith("python")) and not _has_own_program(prog, rest):
                    if heredocs.get(prog, 0) > 0:
                        heredocs[prog] -= 1  # this one reads its own heredoc, whose body is read through runner_bodies
                    else:
                        return None
            if script is not None:
                sub = _searches(script, cur, depth + 1, bodies)
                if sub is None:
                    return None
                recursive, folders, words = recursive or sub[0], folders + sub[1], words + sub[2]
                continue
            rec, walks = _search_of(prog, rest, cur, xargs)
            recursive = recursive or rec
            if walks:
                folders.append(cur)
    return recursive, folders, words


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

    Every listed path the full text names is refused, prose and heredoc bodies included. Whether the command SEARCHES is read from its
    commands (issue 130): heredoc text no shell reads and quoted words are data, each command is judged by itself, and a search that names a
    root walks only that root; the working folder is walked only by a search that names none. A segment that only starts a lane (lane.sh) is
    not a search (issue 233): its folder argument is a working folder, which the launcher itself refuses when it is listed and whose tool
    calls the lane's own hook guards. When the text cannot be read with certainty the old, wider reading is used (_bash_hits_wide)."""
    cwd = cwd or os.getcwd()
    runner_bodies = []
    receivers = {}
    info = _searches(_strip_heredocs(command, runner_bodies, receivers), cwd, bodies=runner_bodies, heredocs=receivers)
    if info is None:
        segs = _segments(command)
        if any(_is_launcher(seg) for seg in segs):
            r = _bash_hits_wide(command, cwd, paths, searching=False)
            if r:
                return r
            command = " ; ".join(seg for seg in segs if not _is_launcher(seg))
        return _bash_hits_wide(command, cwd, paths)
    r = _bash_hits_wide(command, cwd, paths, searching=False)
    if r:
        return r
    for body in runner_bodies:  # what an interpreter runs from its heredoc can search: the roots it names count (not the working-folder guess)
        r = _bash_hits_wide(body, cwd, paths, walk_cwd=False)
        if r:
            return r
        # ... and a string of its code that runs a recursive search with no root walks the working folder, as the shell's own command would
        for lit in _code_commands(body):
            if not _SEARCH_WORD.search(lit):
                continue
            sub = _searches(lit, cwd)
            r = _info_hit(sub, cwd, paths) if sub is not None else _bash_hits_wide(lit, cwd, paths)
            if r:
                return r
    return _info_hit(info, cwd, paths)


_SEARCH_WORD = re.compile(r"\b(?:grep|egrep|fgrep|zgrep|rg|ag|ack|fd|fdfind|find|tree|du|ls|locate|plocate|mlocate)\b")


_LIT = r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*\""""
_RUNS_A_SHELL = re.compile(r"\b(?:system|popen|exec\w*|spawn\w*|run|call|check_output|check_call|Popen|shell_exec|passthru|proc_open|qx)\s*\(\s*"
                           r"(?:\[((?:\s*(?:%s)\s*,?)*)\]|(%s))|`([^`]*)`" % (_LIT, _LIT))


def _code_commands(code):
    """The command lines a piece of interpreter code hands to a call that runs a shell or a program (system("..."), os.system('...'),
    subprocess.run(['grep', ...]), execSync('...'), `...`), each as one string. A quoted word that goes anywhere else is prose or data: it is not read as
    a search, which is what keeps `print('find it')` an ordinary line of code."""
    out = []
    for m in _RUNS_A_SHELL.finditer(code):
        if m.group(1) is not None:
            out.append(" ".join(x[1:-1] for x in re.findall(_LIT, m.group(1))))
        elif m.group(2) is not None:
            out.append(m.group(2)[1:-1])
        else:
            out.append(m.group(3))
    return out


def _info_hit(info, cwd, paths):
    """The listed path or folder a parsed search (_searches' answer) walks into, as (entry, "root"), or None."""
    recursive, folders, words = info
    if not recursive:
        return None
    cur = cwd
    for i, w in enumerate(words):
        for base in {cur, cwd}:
            for piece in [w] + [p for p in re.split(r"[=:,]", w) if p and p != w]:
                if not piece or (piece.startswith("-") and "/" not in piece and "~" not in piece):
                    continue
                full = expand(piece, base)
                for cand in [full] + _expand_globs(full):
                    hit = paths.contains(cand, base)
                    if hit:
                        return hit, "root"
        if w == "cd" and i + 1 < len(words):
            cur = expand(words[i + 1], cur)
    for folder in folders:  # a search with no folder named walks the working folder
        hit = paths.contains(folder, folder)
        if hit:
            return hit, "root"
    for tok in text_paths(" ".join(words)):
        hit = paths.contains(expand(tok, cwd), cwd)
        if hit:
            return hit, "root"
    return None


def _bash_hits_wide(command, cwd, paths, searching=True, walk_cwd=True):
    """walk_cwd=False leaves out the guess that a search naming no root walks the working folder (used for the body of an interpreter's
    heredoc, where a word like `find` is as likely prose or code as a search); the roots a body names are checked either way."""
    words = _words(command)
    # Each command is judged on its own words (issue 146's reading): over the whole text, `grep -c x f; gh pr view -R owner/name` read gh's -R
    # (its --repo) as grep's -R. A text that cannot be read with certainty is still one group of all its words, as before.
    recursive = searching and any(_is_recursive(g) for g in _command_word_groups(command))
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
    if recursive and walk_cwd:  # a search with no folder named walks the working folder
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
    """Files no session may change: the list itself, and the hook, the module and its bash helper in the wall's own folder. The three places
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


_TEXT_ONLY_PROGRAMS = {"grep", "egrep", "fgrep", "echo", "cat"}  # they print or search their arguments and never run them
_STDIN_FILTERS = _TEXT_ONLY_PROGRAMS | {"head", "tail", "wc", "cut", "tr", "nl", "sort", "uniq"}  # none runs what it reads on stdin (sort can, with --compress-program: see below)
_REDEFINERS = {"alias", "function", "hash", "enable", "eval", "source", ".", "builtin", "command", "declare", "typeset", "export", "readonly",
               "local", "unset", "set", "shopt", "trap", "exec", "env"}  # each can make a plain name such as grep run something else, or
#                run a word that can: it changes a name, the search path or a shell option, or starts another builtin by name
_COMPOUND_WORDS = {"{", "}", "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done", "case", "esac", "select", "in",
                   "time", "!", "[[", "]]", "coproc"}  # a block's output can be piped as a whole: `{ echo '..'; } | bash`
_HARMLESS_REDIRECT = re.compile(r">(?:&[12]|>?[ \t]*/dev/null)(?=[ \t\n;&|<>)]|$)")  # `>&2`, `2>&1`, `>/dev/null`: no file is written. What
#                      follows must END the bash word: a quote or backslash glued on makes it part of a longer word, which names a file
_FILTER_WORD_UNSURE = re.compile(r"[^A-Za-z0-9_./=,:+@%<>&-]")  # in a word of sort or uniq: anything the shell could expand
_SHELL_SYNTAX = set("<>|;&()`\n")
_OPERATOR_CHARS = set(";|&<>()")


def _quoted_text_as_text(command):
    """The command with the text a search or print program was handed taken out of the write check's sight: a `>` inside the quoted
    pattern of `grep -E 'a|=>|b' file`, or in the body of `cat > notes.md <<'EOF'`, is text and writes nothing (issue 71: three false
    refusals in one night). Only two things change, and only in a pipeline where EVERY command is, by its bare first word, one of
    _STDIN_FILTERS (so no interpreter, xargs or tee reads the text): inside the quotes of a _TEXT_ONLY_PROGRAMS command the characters the
    checks below read as shell syntax become spaces, and the body of such a command's heredoc is dropped. Every character outside quotes
    is returned as it was, so a real redirect, a real `rm`, and anything handed to bash -c, eval, sh, python or tmux is judged as before.
    A pipeline that carries on after a `|` at the end of a line is read as one pipeline. A pipeline that fills a file (a redirect other
    than `>&1`, `>&2` or `>/dev/null`, `sort -o`, `uniq IN OUT`) keeps the old reading unless every command in the whole text is one of
    _STDIN_FILTERS, so `echo '..' > x.sh; bash x.sh` is still judged by the text it wrote.
    The command comes back UNCHANGED (the old, stricter reading) whenever its text cannot be read with certainty or could run the quoted
    text after all: a command substitution, backtick, parenthesis or `$'..'` anywhere outside single quotes, a comment, a `${..}` that
    is more than a name, an unclosed quote or heredoc, a command anywhere in the text that can change what a name runs (_REDEFINERS, a
    variable assignment, or a program name that is not a plain word), or a block
    word (`{`, if, then, for, while, do, done, case ...) at the start of a command, because a block's output can be piped as a whole.
    A sort or uniq whose words hold anything the shell could expand (a variable, wildcard or brace) or a `--` word, and a sort with an
    option starting `--c` (--compress-program runs a program on the lines), is not counted as a filter. Words end at bash's blanks only (space, tab,
    newline), so a program name with a carriage return glued on is not the plain name; `<&` is a redirect, never the end of a command."""
    n = len(command)
    pipelines = []
    cmds = []  # the pipeline being read, one entry per command (see new_command)

    def new_command():  # first: its bare first word or None; spans: its quoted texts; bodies: its heredoc bodies; at, end: where its
        return {"first": None, "spans": [], "bodies": [], "at": None, "end": None, "writes": False}  # text sits; writes: it fills a file

    cmd = new_command()
    acc, done, bad = "", False, False  # the first word so far; it has ended; it cannot be trusted (quoted, escaped, after a redirect)
    pending = []  # heredocs opened on this line: (end word, strip tabs, quoted, command)
    i = 0

    def close_word():
        nonlocal done
        if acc and not done:
            done = True

    def close_command():
        nonlocal cmd, acc, done, bad
        cmd["first"] = None if bad else acc
        cmd["end"] = i
        if acc or bad or cmd["spans"] or cmd["bodies"]:
            cmds.append(cmd)
        cmd = new_command()
        acc, done, bad = "", False, False

    def close_pipeline():
        nonlocal cmds
        close_command()
        if cmds:
            pipelines.append(cmds)
        cmds = []

    while i < n:
        c = command[i]
        if c == "\\":
            if not done:
                bad = True
            i += 2
            continue
        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                return command
            if not done:
                bad = True
            cmd["spans"].append((i + 1, j))
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                j += 2 if command[j] == "\\" else 1
            if j >= n or "`" in command[i:j] or "$(" in command[i:j]:
                return command
            if not done:
                bad = True
            cmd["spans"].append((i + 1, j))
            i = j + 1
            continue
        if c in "`()":
            return command
        if c == "$":
            nxt = command[i + 1:i + 2]
            if nxt in ("'", '"'):
                return command
            if nxt == "{":
                m = re.match(r"\$\{\w+\}", command[i:])
                if not m:
                    return command
                if not done:
                    bad = True
                i += m.end()
                continue
            if not done:
                bad = True
            i += 1
            continue
        if c == "#" and (i == 0 or command[i - 1].isspace() or command[i - 1] in _OPERATOR_CHARS):
            return command
        if c == "\n":
            pos = i + 1  # the bodies of the heredocs opened on this line follow it, in order
            for end_word, strip, quoted, owner in pending:
                start = pos
                while True:
                    if pos >= n:
                        return command  # the heredoc never ends
                    eol = command.find("\n", pos)
                    line = command[pos:] if eol < 0 else command[pos:eol]
                    pos = n if eol < 0 else eol + 1
                    if (line.lstrip("\t") if strip else line) == end_word:
                        break
                if not quoted and ("`" in command[start:pos] or "$(" in command[start:pos]):
                    return command  # an unquoted heredoc runs the substitutions in its body
                owner["bodies"].append((start, pos))  # the body, its end line and that line's newline
            pending = []
            if not (cmds and not (acc or bad or cmd["spans"] or cmd["bodies"])):  # after a `|` the pipeline goes on on the next line
                close_pipeline()
            i = pos
            continue
        if c == "<":
            if command.startswith("<<<", i):
                close_word()
                if not acc:
                    bad = True
                i += 3
                continue
            if command.startswith("<<", i):
                j = i + 2
                strip = command[j:j + 1] == "-"
                if strip:
                    j += 1
                while j < n and command[j] in " \t":
                    j += 1
                k = j
                while k < n and command[k] not in " \t\n" and command[k] not in _OPERATOR_CHARS:
                    k += 1
                raw = command[j:k]
                end_word = re.sub(r"['\"\\]", "", raw)
                if not end_word or "$" in raw or "`" in raw or raw.count("'") % 2 or raw.count('"') % 2:
                    return command
                close_word()
                if not acc:
                    bad = True
                pending.append((end_word, strip, end_word != raw, cmd))
                i = k
                continue
            close_word()
            if not acc:
                bad = True
            i += 1
            if command[i:i + 1] == "&":  # `<&0`, `0<&0`, `<&-`: a copy of an open file, never the `&` that ends a command
                i += 1
            continue
        if c == ">":
            close_word()
            if not acc:
                bad = True  # `>out grep ...`: the next word is a file, not the program
            if not _HARMLESS_REDIRECT.match(command, i):
                cmd["writes"] = True
            i += 1
            if command[i:i + 1] == ">":
                i += 1
            if command[i:i + 1] in ("&", "|"):
                i += 1
            continue
        if c == "&":
            if command[i + 1:i + 2] == ">":  # `&>file`: a redirect, not the end of the command
                close_word()
                if not acc:
                    bad = True
                if not _HARMLESS_REDIRECT.match(command, i + 1):
                    cmd["writes"] = True
                i += 2
                if command[i:i + 1] == ">":
                    i += 1
                continue
            close_pipeline()
            i += 2 if command[i + 1:i + 2] == "&" else 1
            continue
        if c == ";":
            close_pipeline()
            i += 1
            continue
        if c == "|":
            if command[i + 1:i + 2] == "|":
                close_pipeline()
                i += 2
            else:
                close_command()
                i += 2 if command[i + 1:i + 2] == "&" else 1
            continue
        if c in " \t":  # bash's own blanks; a carriage return or a no-break space stays part of the word (`echo\r` is not echo)
            close_word()
            i += 1
            continue
        if not done:
            if not acc:
                cmd["at"] = i
            acc += c
        i += 1
    if pending:
        return command  # a heredoc with no body
    close_pipeline()

    everything = [c for p in pipelines for c in p]
    # One command anywhere in the text whose program cannot be read as a plain name (quoted, escaped, a variable, after a redirect), that
    # sets a variable (`PATH=...`, `X=1 grep`), or that can change what a name runs, turns the relaxing off for the whole text.
    if any(not c["first"] or "=" in c["first"] or c["first"] in _REDEFINERS or c["first"] in _COMPOUND_WORDS for c in everything):
        return command
    for c in everything:  # two of the filters can fill a file without a redirect: `sort -o FILE` and `uniq INPUT OUTPUT`
        if c["first"] in ("sort", "uniq"):
            try:
                words = shlex.split(command[c["at"]:c["end"]])[1:]
            except ValueError:
                words = None
            if words is None or any(_FILTER_WORD_UNSURE.search(w) for w in words):
                c["first"] = None  # a variable, wildcard or brace word could turn into any option: keep the old reading
            elif "--" in words:
                c["first"] = None  # after `--` a word starting with "-" is a file, so the count of plain words below would miss it
            elif c["first"] == "sort" and any(w.startswith("--c") for w in words):
                c["first"] = None  # sort --compress-program=PROG hands the lines it reads to PROG
            elif c["first"] == "sort":
                c["writes"] = c["writes"] or any(re.match(r"-[A-Za-z]*o|--o", w) for w in words)
            else:
                c["writes"] = c["writes"] or sum(1 for w in words if w == "-" or not w.startswith("-")) > 1
    only_filters = all(c["first"] in _STDIN_FILTERS for c in everything)  # then nothing in this command can run a file it wrote
    out = list(command)
    for p in pipelines:
        if not all(c["first"] in _STDIN_FILTERS for c in p):
            continue
        if not only_filters and any(c["writes"] for c in p):
            continue  # `echo '..' > x.sh; bash x.sh`: the text lands in a file that another program here could run
        for c in p:
            if c["first"] not in _TEXT_ONLY_PROGRAMS:
                continue
            for a, b in c["spans"]:
                for k in range(a, b):
                    if out[k] in _SHELL_SYNTAX:
                        out[k] = " "
            for a, b in c["bodies"]:
                for k in range(a, b):
                    out[k] = ""
    return "".join(out)


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

    command = _quoted_text_as_text(command)  # a `>` inside the quoted pattern of a grep, or in a cat heredoc's body, is text
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
            ok = [os.path.realpath(os.path.join(home(), ".claude", "scripts", "redact_view.py")), os.path.realpath(os.path.join(wall_dir(), "no_ai_wall.py"))]  # run, not edited
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


def _command_word_groups(command):
    """The words of a shell text, one group for each command in it (issue 146). reads_content_recursively looks for a program word and then an
    option after it; over the whole text's words, `grep -oE x file; gh pr view -R owner/name` read gh's -R (its --repo) as grep's -R (recursive)
    and refused an ordinary read. A quoted word that holds a space is also split into its own words (`bash -c 'grep -r x /dir'` stays a search),
    and a text that cannot be read with certainty (an unclosed quote) is one group of all its words, as before. A text with a substitution
    or a subshell (`$(`, a backtick, any parenthesis) is also read whole: the reader ends a command there, so `grep $(echo -r) x /dir` would
    otherwise put the flag in another group than its grep. So is a text with a here-string or a heredoc (`<<`, which also covers `<<<`): its text
    goes to the receiver as input or as code (`bash <<< "grep -r x /dir"`, `xargs grep x /dir <<< "-r"`, the same with a heredoc), and the reader
    drops it from the command it feeds."""
    cmds = _shell_commands(command)
    if cmds is None or _has_unquoted_paren(command) or "`" in command or "<<" in command:
        return [_words(command)] + _split_groups(cmds)
    return _split_groups(cmds) or [_words(command)]


def _has_unquoted_paren(command):
    """True when a `(` or `)` sits outside quotes and is not backslash-escaped, or when a quote is left open (issue 150). The reader ends a
    command at such a parenthesis (a subshell, `$(`, an array), so it cannot say which command a flag belongs to and the whole text is read
    as one group. A parenthesis inside quotes is text (`sed -i "s/(a)/(b)/"`): the reader keeps it in its word, and it must not force the
    whole-text reading. A comment also gives True, the stricter answer. The scan follows the reader (_shell_commands): a
    backslash takes the next character, single quotes end at the next single quote, double quotes at the next unescaped double quote."""
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
        elif c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                return True
            i = j + 1
        elif c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                j += 2 if command[j] == "\\" else 1
            if j >= n:
                return True
            i = j + 1
        elif c in "()" or (c == "#" and (i == 0 or command[i - 1] in " \t\n;&|")):
            return True  # (a comment: the reader skips its text, this scan would read its quotes, so the stricter answer)
        else:
            i += 1
    return False


def _split_groups(cmds):
    """The per-command word groups of a read text (see _command_word_groups); an empty list when there is no text."""
    if not cmds:
        return []
    groups = []
    for cmd in cmds:
        g = []
        text_flags = any(os.path.basename(w) in _TEXT_VALUE_PROGRAMS for w, q in cmd if not q)
        prev = ""
        for w, q in cmd:
            g.append(w)
            # a title or message handed to gh or git is text, never a program: `gh issue create --title "a grep before gh -R"` has no grep in it
            as_text = text_flags and q and (prev in _TEXT_VALUE_FLAGS or w.split("=", 1)[0] in _TEXT_VALUE_FLAGS and w.startswith(("--title=", "--body=", "--message=")))
            if any(ch.isspace() for ch in w) and not as_text:
                g.extend(_words(w))
            prev = w
        groups.append(g)
    return groups


_TEXT_VALUE_PROGRAMS = ("gh", "git")  # programs whose title, body and message flags (below) only ever carry text (issue 150)
_TEXT_VALUE_FLAGS = ("--title", "--body", "--message", "-m", "-t", "-b")


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
        command = _resolve_vars(str(ti.get("command") or ""))  # `W=/dir; cd $W` read as `cd /dir`, so a relative path after it can be placed
        r = bash_hits(command, cwd, paths)
        if r:
            return deny_root(r[0]) if r[1] == "root" else deny_path(r[0])
        if any(_is_protected(t, cwd, protected_files()) for t in _command_tokens(command, cwd)) and not _read_only_listing(command):
            return deny_protected()
        if bash_writes_guarded(command, cwd):
            return deny_protected()
        if any(reads_content_recursively(g) for g in _command_word_groups(command)):  # `grep -r`, `rg`, `tar` and the like over the list's folder or one above it
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
