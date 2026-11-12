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
  - contains either list file's name, anywhere, also when quotes or backslashes are
    written inside the name;
  - has a word that resolves to a list file or to the installed copy of this hook. A word
    is read the way a shell reads it (quotes and backslashes taken out, so a path glued
    from quoted pieces is one word) and is resolved against the session's folder and
    against every folder the command changes into (cd, pushd, -C), with ~, $HOME, ".."
    and symlinks followed. Every ${HOME...} form, such as ${HOME:-x} or ${HOME%/}, is read
    as the home folder. A "$" written before a quote and a backslash at a line end are
    dropped first, as a shell drops them. A cd with no folder, or with one held in a
    variable, counts as a change into the home folder. "~name" is read as that account's
    home folder (this account's own name as the home folder above), and "~+" and "~-" as
    the current folder. A folder glued to a one-letter option, "-C/tmp" or "-d$HOME/x", is
    checked as a word of its own;
  - has a wildcard or brace word that could match a list file, this hook, or (with a verb
    below) one of the guarded folders, resolved the same way. A brace word is expanded
    the way a shell expands it, a{b,c} into ab and ac, and each result is checked; a word
    with more than 64 results is checked as wildcards instead, where a group that holds a
    "/" counts as any number of folders and any other group counts as any characters
    inside one name. A {1..9} run and an extended wildcard group, @(a|b), count the same
    way. A bracket set, [a-c], is read as written with letter case ignored; a named class,
    [[:alpha:]], or a set that cannot be read counts as any one character;
  - has command output, $(...) or backticks, in the middle or at the end of a name. The
    output counts as any characters inside one name, so ~/.config/afa/$(echo x).txt is
    checked as ~/.config/afa/*.txt. A word that starts with command output is not checked,
    so "rm $(find . -name '*.pyc')" passes;
  - names the .config/afa folder as a whole together with a verb that moves, removes,
    links, copies, unpacks into, walks or re-permissions (mv, rm, cp, unzip, find, chmod
    and the like);
  - runs tar, bsdtar, unzip, cpio, pax, 7z, patch or find while one of the folders it
    changes into, or the session's own folder, is the .config/afa folder. Those verbs
    write into or walk the folder they run in without a word that names it;
  - names ~/.config or ~/.claude/hooks as a whole together with a verb that moves, removes,
    links or re-permissions it.
A verb counts however it is written: rm, /bin/rm, ./rm, \\rm or "rm".

Message text is checked like any other words. The text after "git commit -m" or
"gh pr create --body" is not skipped, because every way of skipping it also skipped a live
command: a quote that is not where a message starts (inside another quote, after a "#" or
in a here-document), text that is run later ("eval $_", an array handed to eval), and git
itself running it ("git bisect run sh -c -m ...", an editor set to sh). So a message that
names a guarded folder beside a verb such as rm, or that spells a list file's name, is
refused; put such a message in a file and pass the file (git commit -F, gh --body-file).

Honest limit: this is a tripwire for direct and careless edits, not a wall. A command that
builds the path from pieces, reaches it through a variable other than HOME, reaches it
through a wildcard in the name of a symlink, or runs a script file that does the edit is
not seen, and a session that can edit the settings file can remove this hook. The sturdy
fix is ownership: lists a session's user cannot write. The session's own safety check and
lane_merge.py's refusals remain the other layers.

Time limit: the settings file stops this hook after 5 seconds, and a hook that is stopped
lets the command through. So a Bash check runs in a child process that this hook stops
itself after 3 seconds, and a command whose check did not finish is refused. Long runs of
braces and brackets are read in one pass, and a run of "any characters" side by side (many
brace groups in one word, "*" next to a group) is matched as one, so ordinary long commands
finish far inside the limit; the limit is there for a command padded to be slow.

Unreadable or non-JSON stdin: allow, as lane-watch-guard.py does. An unexpected error while
checking one word skips that word after one line on stderr and the other words are still
checked, so a fault here cannot stop every shell call and one odd word cannot switch the
checks off; the plain name check runs first and has nothing in it that can fail.
"""
import json
import os
import pwd
import re
import select
import signal
import sys
import warnings

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
# Each relative folder change doubles the list of folders a later word may start from; 64 leaves room for six.
MAX_FOLDERS = 64

# Verbs that act on a folder as a whole. The list folder gets the long list; its parent and
# the hooks folder get the short one, because copying into or searching them is everyday work.
PARENT_VERB_NAMES = frozenset("mv rm rmdir ln chmod chown chattr setfacl rename unlink".split())
# Verbs that write into or walk the folder they run in without naming it as a word. Given a folder to unpack
# into ("unzip x.zip -d folder"), they write its files without naming those either, so they count for both.
CWD_VERB_NAMES = frozenset("tar bsdtar unzip cpio pax 7z 7za 7zr patch find".split())
AFA_VERB_NAMES = PARENT_VERB_NAMES | CWD_VERB_NAMES | frozenset("cp rsync install".split())
FOLDER_CHANGERS = ("cd", "pushd", "-C")
# A cd or pushd with nothing after it goes to the home folder.
BARE_CD = re.compile(r"(?<![\w./-])(?:cd|pushd)(?:[ \t]+--)?[ \t]*(?:$|[;&|)}#\n])")
WORD_SPLIT = re.compile(r"[\s\"';&|()<>=,`]+")
QUOTES = re.compile(r"[\"'\\]")
# Control characters that are not white space. A shell may drop one or stop reading at it, so both readings are checked.
CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
# A quote or a backtick between a "{" and its "}" means the pair is not one brace group; so does white space.
BRACE_BREAKS = "\"'`"
SEQUENCE = re.compile(r"[^./]+\.\.[^./]+(?:\.\.[^./]+)?")
WILD = "*?["
MAX_BRACE_WORDS = 64
MAX_BRACE_GROUPS = 200
# The settings file stops this hook after 5 seconds, and a stopped hook lets the command through. So the hook
# stops its own check sooner and refuses the command itself.
TIME_LIMIT = 3.0
# Stand-ins used only inside this file: "any number of folders", and the braces and commas of a brace group.
ANY, LEFT, RIGHT, COMMA = "﷐", "﷑", "﷒", "﷓"
# Two more stand-ins, both "any characters inside one name": ONE for a group whose results cannot be listed
# (a {1..9} run, a brace word with too many results, an extended wildcard group), SUB for a command's output.
ONE, SUB = "﷔", "﷕"
# What the stand-ins and "*" become in a regular expression: any characters inside one name, or across folders.
NAME_RUN, PATH_RUN = "[^/]*", ".*"
# A "$" right before a quote ($'' and $"") is part of the quote, and a backslash before a line end joins the lines.
DOLLAR_QUOTE = re.compile(r"\$(?=[\"'])")
HOME_FORM = re.compile(r"\$\{HOME(?![A-Za-z0-9_])[^}]*\}")
SUBSTITUTION = re.compile(r"\$\([^()]*\)|`[^`]*`")
EXTENDED_GROUP = re.compile(r"[@+!?*]\([^()]*\)")
MAX_FOLDS = 20
# A folder glued to a one-letter option, as in "-C/tmp", "-d$HOME/x" or "-o./out". The folder is checked as a word too.
GLUED_OPTION = re.compile(r"-[A-Za-z]([~/$.].*)")
# "~name" at the start of a word is that account's home folder; "~+" and "~-" are the current and the previous folder.
TILDE_NAME = re.compile(r"~([A-Za-z0-9_.+-]+)(?=/|$)")


class TooManyWords(Exception):
    pass


def forms(path):
    """The path as written and with symlinks followed."""
    out = [os.path.abspath(path)]
    real = os.path.realpath(path)
    if real not in out:
        out.append(real)
    return out


def reason_for(what):
    if what == "self":
        return (
            "BLOCKED: this is the installed copy of list-file-guard.py; a session may not change it. "
            "A person copies hooks in a plain terminal."
        )
    if what == "slow":
        return (
            "BLOCKED: this command took the merge-list guard too long to read, so it was not run. "
            "Split it into separate commands, or move long text into a file."
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
        "link, copy into or re-permission it. A person does that in a plain terminal. If the folder is only "
        "named in a commit message or other message text, put the message in a file and pass the file."
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


def home_of(name):
    """The home folder a shell gives "~name", or None when there is no such account."""
    try:
        if name == pwd.getpwuid(os.getuid()).pw_name:
            return HOME
        return pwd.getpwnam(name).pw_dir
    except (KeyError, OSError):
        return None


def expand(word):
    if word == "~" or word.startswith("~/"):
        word = HOME + word[1:]
    elif word.startswith("~"):
        named = TILDE_NAME.match(word)
        if named and named.group(1)[0] in "+-":
            # The current or the previous folder. Either is read as "here", which is then checked against the
            # session's folder and every folder the command changes into.
            word = "." + word[named.end():]
        elif named and home_of(named.group(1)):
            word = home_of(named.group(1)) + word[named.end():]
    # Every ${HOME...} form (${HOME:-x}, ${HOME%/}) is read as the home folder itself.
    return HOME_FORM.sub(lambda match: HOME, word).replace("$HOME", HOME)


def mark_braces(text):
    """Swap the braces and commas of every brace group for stand-ins, so cutting the text into words keeps a group whole.

    A group is a "{" and its "}" with no white space, quote or backtick anywhere between them. One pass over the
    text, so a long run of braces costs no more than its length.
    """
    if "{" not in text:
        return text
    opened, role = [], {}
    for i, ch in enumerate(text):
        if ch == "{":
            opened.append(i)
        elif ch == "}":
            if opened:
                role[opened.pop()] = LEFT
                role[i] = RIGHT
        elif ch in BRACE_BREAKS or ch.isspace():
            opened.clear()
    if not role:
        return text
    out, depth = [], 0
    for i, ch in enumerate(text):
        mark = role.get(i)
        if mark == LEFT:
            depth += 1
        elif mark == RIGHT:
            depth -= 1
        out.append(mark or (COMMA if ch == "," and depth else ch))
    return "".join(out)


def unmark(word):
    return word.replace(LEFT, "{").replace(RIGHT, "}").replace(COMMA, ",")


def expand_braces(word):
    """Every word a shell makes from this one by brace expansion: a{b,c}d gives abd and acd. A {1..9} run becomes ONE."""
    start = word.find(LEFT)
    if start == -1:
        return [word]
    if word.count(LEFT) > MAX_BRACE_GROUPS:
        # This function calls itself once per group; a word with more groups than this is checked as wildcards.
        raise TooManyWords()
    depth, end, cuts = 0, -1, []
    for i in range(start, len(word)):
        ch = word[i]
        if ch == LEFT:
            depth += 1
        elif ch == RIGHT:
            depth -= 1
            if depth == 0:
                end = i
                break
        elif ch == COMMA and depth == 1:
            cuts.append(i)
    if end == -1:
        return [word]
    tails = expand_braces(word[end + 1:])
    if cuts:
        bounds = [start] + cuts + [end]
        middles = [alt for a, b in zip(bounds, bounds[1:]) for alt in expand_braces(word[a + 1:b])]
    elif SEQUENCE.fullmatch(word[start + 1:end]):
        middles = [ONE]
    else:
        # No comma and no run: a shell leaves the braces as written.
        middles = ["{" + inner + "}" for inner in expand_braces(word[start + 1:end])]
    if len(middles) * len(tails) > MAX_BRACE_WORDS:
        raise TooManyWords()
    return [word[:start] + middle + tail for middle in middles for tail in tails]


def collapse_braces(word):
    """For a word with too many expansions: each group becomes ONE, or any number of folders when it holds a "/"."""
    # One pass, innermost group first: pieces[-1] collects the text of the group being read, pieces[0] the result.
    pieces = [[]]
    for ch in word:
        if ch == LEFT:
            pieces.append([])
        elif ch == RIGHT and len(pieces) > 1:
            body = "".join(pieces.pop())
            pieces[-1].append(ANY if ("/" in body or ANY in body) else ONE)
        else:
            pieces[-1].append(ch)
    while len(pieces) > 1:
        # A stand-in that was typed into the command and never closes stays as written.
        body = "".join(pieces.pop())
        pieces[-1].append(LEFT + body)
    return "".join(pieces[0])


def words_of(text):
    """The text cut into words, with each brace word replaced by the words a shell would make from it."""
    out = []
    for word in WORD_SPLIT.split(mark_braces(text)):
        if not word:
            continue
        try:
            made = expand_braces(word)
        except TooManyWords:
            made = [collapse_braces(word)]
        out.extend(unmark(w) for w in made if w)
    return out


def fold_groups(text):
    """The text with each extended wildcard group, @(a|b), swapped for ONE and each $(...) or backtick group for SUB."""
    for _ in range(MAX_FOLDS):
        folded = EXTENDED_GROUP.sub(ONE, SUBSTITUTION.sub(SUB, text))
        if folded == text:
            break
        text = folded
    return text


def set_regex(part, i):
    """The bracket set that opens at part[i] as a regular expression, and the index after it; (None, i) if it never closes."""
    start = i + 1
    negated = start < len(part) and part[start] in "!^"
    if negated:
        start += 1
    j = start + 1 if part[start:start + 1] == "]" else start
    named = False
    unclosed = set()
    while j < len(part) and part[j] != "]":
        kind = part[j + 1:j + 2]
        if part[j] == "[" and kind in (":", ".", "=") and kind not in unclosed:
            named = True
            end = part.find(kind + "]", j + 2)
            if end != -1:
                j = end + 2
                continue
            # A class that never closes is plain characters to a shell. It is looked for once per kind, so a
            # long run of openers costs no more than its length; the set still falls back to any one character.
            unclosed.add(kind)
        j += 1
    if j >= len(part):
        return None, i
    body = part[start:j]
    wide = "[^/]"
    if named or not body:
        # A named class, [[:alpha:]], is not worked out: any one character, wider than written and never narrower.
        return wide, j + 1
    members = "".join(ch if ch == "-" else re.escape(ch) for ch in body)
    # Letter case is ignored, because some locales sort upper and lower case together inside a range.
    candidate = "(?i:[%s%s])" % ("^/" if negated else "", members)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            re.compile(candidate)
    except (re.error, Warning):
        return wide, j + 1
    return candidate, j + 1


def glob_regex(pattern, dots):
    """A wildcard path as a regular expression over a whole path. "**" and the any-folders stand-in cross "/"."""
    out = []

    def any_run(piece):
        # Two "any characters" side by side match what one does, and a run of them left as written makes a
        # failed match try every way of sharing the name out between them: 30 in a row took a second.
        if out and out[-1] in (NAME_RUN, PATH_RUN):
            if piece == PATH_RUN:
                out[-1] = PATH_RUN
        else:
            out.append(piece)

    for part in pattern.split("/"):
        if not part:
            continue
        out.append("/")
        # As in a shell, a part that starts with a wildcard does not match a name that starts with a dot,
        # unless the command switches that on (dots is True).
        if not dots and (part[0] in "?[" or (part[0] == "*" and not part.startswith("**"))):
            out.append(r"(?!\.)")
        # A "[" after the last "]" cannot open a set, so it is never read as one: a long run of "[" stays cheap.
        last_close = part.rfind("]")
        i = 0
        while i < len(part):
            ch = part[i]
            piece, after = None, i
            if ch == "[" and i < last_close:
                piece, after = set_regex(part, i)
            if piece:
                out.append(piece)
                i = after
                continue
            if ch == ANY:
                any_run(PATH_RUN)
            elif ch in (ONE, SUB):
                # Inside one name only, and it may start with a dot: its real results are not known here.
                any_run(NAME_RUN)
            elif ch == "*":
                j = i
                while j < len(part) and part[j] == "*":
                    j += 1
                any_run(PATH_RUN if j - i > 1 else NAME_RUN)
                i = j
                continue
            elif ch == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(ch))
            i += 1
    return re.compile("".join(out), re.S)


def wild_patterns(candidate, dots):
    """The wildcard path as written, and with the symlinks in its plain leading folders followed."""
    parts = candidate.split("/")
    for i, part in enumerate(parts):
        if ANY in part or "**" in part:
            # ".." after a part of unknown depth cannot be worked out from the text: the rest is any depth.
            if ".." in parts[i + 1:]:
                parts = parts[:i] + [ANY]
            break
    pattern = os.path.normpath("/".join(parts))
    out = [pattern]
    pieces = [p for p in pattern.split("/") if p]
    first = next((i for i, p in enumerate(pieces) if any(c in p for c in WILD + ANY + ONE + SUB)), len(pieces))
    real = "/".join([os.path.realpath("/" + "/".join(pieces[:first]))] + pieces[first:])
    if real not in out:
        out.append(real)
    return [glob_regex(p, dots) for p in out]


def could_name(regexes, target):
    return any(rx.fullmatch(form) for rx in regexes for form in forms(target))


def folders_of(words, cwd, home_too):
    """The session's folder plus every folder the command changes into, or None when there are too many."""
    bases = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        bases.append(os.path.normpath(cwd))
    else:
        # No folder given: the hook runs in the session's folder, and the home folder is the other safe guess.
        bases.append(os.getcwd())
        home_too = True
    if home_too and HOME not in bases:
        bases.append(HOME)
    targets = set()
    for i, word in enumerate(words):
        if word.startswith("-C") and len(word) > 2:
            # "-Cfolder", the folder glued to the option.
            target = expand(word[2:])
        elif word not in FOLDER_CHANGERS:
            continue
        else:
            j = i + 1
            while word != "-C" and j < len(words) and words[j].startswith("-") and words[j] != "-":
                j += 1
            if j >= len(words):
                continue
            targets.add(j)
            target = expand(words[j])
        if "$" in target:
            # A folder held in a variable cannot be read here; the home folder stands in for it.
            new = [HOME]
        elif os.path.isabs(target):
            new = [os.path.normpath(target)]
        else:
            new = [os.path.normpath(os.path.join(b, target)) for b in bases]
        for folder in new:
            if folder not in bases:
                bases.append(folder)
        if len(bases) > MAX_FOLDERS:
            return None, targets
    return bases, targets


def check_word(word, bases, afa_verb, parent_verb, is_cd_target, dots=False):
    word = expand(word)
    if not word or word.startswith(SUB):
        # A word that begins with a command's output is a path this hook cannot read (the honest limit above).
        return None
    wild = any(c in word for c in WILD + ANY + ONE + SUB)
    candidates = [word] if os.path.isabs(word) else [os.path.join(b, word) for b in bases]
    for candidate in candidates:
        if wild:
            regexes = wild_patterns(candidate, dots)
            for protected in PROTECTED:
                if could_name(regexes, protected):
                    return os.path.basename(protected)
            if could_name(regexes, SELF):
                return "self"
            if afa_verb and could_name(regexes, AFA):
                return ".config/afa"
            if parent_verb and could_name(regexes, CONFIG):
                return ".config"
            if parent_verb and could_name(regexes, HOOKS):
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


def readings(command):
    """The command read each way a shell might: control characters as gaps or dropped, quotes as gaps or taken out."""
    texts = [CONTROL.sub(" ", command)]
    dropped = CONTROL.sub("", command)
    if dropped not in texts:
        texts.append(dropped)
    for text in list(texts):
        bare = QUOTES.sub("", DOLLAR_QUOTE.sub("", text.replace("\\\n", "")))
        if bare not in texts:
            texts.append(bare)
    return texts


def is_list_folder(folder):
    return folder == AFA or os.path.realpath(folder) == os.path.realpath(AFA)


def check_bash(command, cwd):
    texts = readings(command)
    for text in texts:
        for name in BASE_NAMES:
            if name in text:
                return name
    # From here on the words are checked. The text after "git commit -m" is checked like any other words: every
    # way of skipping it also skipped a live command (a quote that is not where a message starts, "eval $_",
    # "git bisect run sh -c -m ...", an editor set to sh). A message that trips a check goes in a file.
    home_too = any(BARE_CD.search(text) for text in texts)
    word_lists = []
    for text in texts:
        for reading in (text, fold_groups(text)):
            words = words_of(reading)
            if words not in word_lists:
                word_lists.append(words)
    for words in word_lists:
        for word in words:
            for name in BASE_NAMES:
                if name in word:
                    return name
    names = {os.path.basename(w) for words in word_lists for w in words}
    afa_verb = bool(names & AFA_VERB_NAMES)
    parent_verb = bool(names & PARENT_VERB_NAMES)
    cwd_verb = bool(names & CWD_VERB_NAMES)
    # A command that sets one of these lets "*" match names that start with a dot.
    dots = bool(names & {"dotglob", "GLOBIGNORE"})
    warned = False
    for words in word_lists:
        try:
            bases, cd_targets = folders_of(words, cwd, home_too)
            # tar, unzip or find in the list folder itself acts on it without a word that names it.
            in_list_folder = cwd_verb and bases is not None and any(is_list_folder(b) for b in bases)
        except Exception as exc:
            print(f"list-file-guard internal error: {type(exc).__name__}", file=sys.stderr)
            continue
        if bases is None:
            return "folders"
        if in_list_folder:
            return ".config/afa"
        for i, word in enumerate(words):
            try:
                hit = check_word(word, bases, afa_verb, parent_verb, i in cd_targets, dots)
                glued = GLUED_OPTION.fullmatch(word)
                if not hit and glued:
                    hit = check_word(glued.group(1), bases, afa_verb, parent_verb, False, dots)
            except Exception as exc:
                # One word the checks cannot read is skipped; every other word is still checked.
                if not warned:
                    print(f"list-file-guard internal error: {type(exc).__name__}", file=sys.stderr)
                    warned = True
                continue
            if hit:
                return hit
    return None


def decide(payload):
    """Exit code for one tool call: 2 with the reason on stderr, or 0."""
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


def decide_in_time(payload):
    """decide(), run in a child process that is stopped after TIME_LIMIT seconds; a check that is stopped refuses.

    The child holds one end of a pipe and the parent waits on the other, so the parent wakes the moment the child
    ends, however it ends, and a check stuck inside one long step can still be stopped from outside.
    """
    if not hasattr(os, "fork"):
        return decide(payload)
    read_end, write_end = os.pipe()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            os.close(read_end)
            code = decide(payload)
        except BaseException as exc:
            # A fault in the whole check: one line, and exit 1, which does not refuse the command.
            print(f"list-file-guard internal error: {type(exc).__name__}", file=sys.stderr)
        finally:
            try:
                sys.stderr.flush()
            finally:
                os._exit(code)
    os.close(write_end)
    try:
        ended, _, _ = select.select([read_end], [], [], TIME_LIMIT)
    finally:
        os.close(read_end)
    if not ended:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        print(reason_for("slow"), file=sys.stderr)
        return 2
    _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") == "Bash":
        return decide_in_time(payload)
    return decide(payload)


if __name__ == "__main__":
    sys.exit(main())
