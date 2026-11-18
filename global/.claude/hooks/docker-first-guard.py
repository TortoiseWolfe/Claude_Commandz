#!/usr/bin/env python3
"""Claude Code PreToolUse hook (Bash): enforce Docker-first for development commands.

Verbatim rule from Jonathan (18 Nov 2026):
  "always Docker first! for all our projects"

Refuses a command that runs a package manager or dev server on the host:
  - npm, npx, pnpm, pnpx, yarn, bun, bunx with install, ci, run, test, exec, start, dev, build, dlx
    (and the short spellings i, t, x, it, cit, ic, add, run-script, install-test);
  - yarn, pnpm and bun also run a project script by name (yarn lint, pnpm vitest), so for those three
    any subcommand outside a short read-only list is refused, and a bare yarn (it installs) too;
  - vite, next (dev, start, build, preview, or bare), expo start; also when called by path
    (./node_modules/.bin/vite);
when the command is not docker run, docker exec or docker compose (docker-compose).

Heredoc bodies (cat <<'EOF' ... EOF) are text, not commands: their lines are never split into commands,
so "done; next one" in a hand-off note passes. The body is still checked where the shell would run it:
a $( ) or backtick in an unquoted-delimiter body, and a body fed to sh or bash (bash <<EOF).

Looked through, so they cannot hide a host run: chains (&& || ; | newline &), $( ) and backticks
(also inside double quotes), sh -c / bash -lc strings, subshells and groups, if/then/do keywords,
wrappers (sudo, env, nice -n 5, timeout 10, nohup, xargs, corepack) and find -exec.

Allowed: anything inside docker run / exec / compose; git, gh, python3 and other everyday commands;
npm --version and the like; a mention of npm in a quoted string, an echo, a grep pattern or a
commit message; a command whose folder (the session's, or one it changes into) is inside a path
in ~/.config/afa/docker-first-exempt.txt.

stdin: {"tool_name": "Bash", "tool_input": {"command": ...}, "cwd": ...}
block: reason on stderr, exit 2.  allow: exit 0.  The reason never echoes the command.

Exemption list: ~/.config/afa/docker-first-exempt.txt. Path prefixes, one per line; blank lines and
# lines are ignored; a missing or unreadable file means no exemptions. This hook only reads it and
never writes it: entries go in only on Jonathan's word.

Fails open: unreadable or non-JSON stdin, another tool, a command that is not a string, or an
unexpected error while checking all exit 0 (an error also prints one line on stderr). A fault here
must never stop every shell call, and this hook is a reminder of the house rule, not a wall: a
script file that runs npm, an alias, or a multi-line $( ) is not seen.
"""
import json
import os
import re
import shlex
import sys

RULE_QUOTE = '"always Docker first! for all our projects"'
REFUSAL_MESSAGE = (
    f'BLOCKED: {RULE_QUOTE} — run this command inside docker run, docker exec, '
    'or docker compose (see the README container recipe of the repo you are in).'
)

EXEMPT_PATH = os.path.expanduser("~/.config/afa/docker-first-exempt.txt")

TARGET_SUBCOMMANDS = frozenset({
    "install", "ci", "run", "test", "exec", "start", "dev", "build", "dlx",
    "i", "t", "add", "run-script",
    # npm's other spellings of the same things
    "x", "it", "cit", "ic", "install-test", "install-ci-test", "install-clean",
})
# yarn, pnpm and bun run a project script by name (yarn lint, pnpm vitest), so for those three any
# subcommand outside this read-only list counts as running something.
READONLY_SUBCOMMANDS = frozenset({
    "help", "version", "config", "info", "list", "ls", "why", "bin", "cache", "outdated",
    "audit", "whoami", "login", "logout", "licenses", "root", "store", "pm",
})
SCRIPT_RUNNERS = frozenset({"pnpm", "yarn", "bun"})
VALUE_FLAGS = frozenset({"--prefix", "-C", "--cwd", "-w", "--workspace", "--filter", "-F"})

STANDALONE_REFUSE = frozenset({"vite", "next"})
ALLOWED_FLAG_ONLY = frozenset({"--version", "-v", "--help", "-h"})

# Words that start a command without being the command. For a wrapper, the set holds the flags that
# take a separate value, so `nice -n 5 npm ci` finds npm.
WRAPPERS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-r", "-t", "-U", "--user", "--group"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "xargs": {"-n", "-P", "-L", "-I", "-d", "-s", "-E", "-a"},
    "stdbuf": {"-i", "-o", "-e"},
    "time": set(), "nohup": set(), "setsid": set(), "exec": set(),
    "command": set(), "builtin": set(), "corepack": set(),
}
SHELL_WORDS = frozenset({"{", "!", "if", "then", "else", "elif", "while", "until", "do"})
SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})

QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
SINGLE_QUOTED = re.compile(r"'[^']*'")
SEPARATOR = re.compile(r"&&|\|\||\|&|\||;|(?<![><])&(?![><&])|\n")
SUBSTITUTION = re.compile(r"\$\((.*?)\)|`([^`]*)`")
# In an unquoted-delimiter heredoc body the shell runs $( ) and backticks, but a backslash shields them.
HEREDOC_SUBSTITUTION = re.compile(r"(?<!\\)\$\((.*?)\)|(?<!\\)`([^`]*)`")
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
# A heredoc operator, found in text whose quoted strings are already masked with Q (so a quoted
# delimiter shows as QQQQQ). << followed by a letter or _ is a delimiter; <<< (herestring) and
# arithmetic like 1<<2 are not.
HEREDOC_OP = re.compile(r"(?<![<\d])<<(?!<)(-?)[ \t]*(Q+|\\?[A-Za-z_]\w*)")


def read_exemptions():
    """Path prefixes from the exemption list, normalized. [] when the file is missing or unreadable."""
    try:
        with open(EXEMPT_PATH, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    exemptions = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        exemptions.append(os.path.normpath(os.path.expanduser(line)))
    return exemptions


def is_exempt(path, exemptions):
    """True if path is, or is inside, an exempt folder."""
    if not path or not exemptions:
        return False
    norm = os.path.normpath(os.path.expanduser(path))
    for ex in exemptions:
        if norm == ex or norm.startswith(ex.rstrip(os.sep) + os.sep):
            return True
    return False


def get_tokens(segment):
    """Split one shell command segment into words. A stray ( or ) from a subshell is dropped."""
    segment = segment.strip()
    if segment.startswith("("):
        segment = segment.lstrip("( \t")
    while segment.endswith(")") and segment.count(")") > segment.count("("):
        segment = segment[:-1].rstrip()
    try:
        return shlex.split(segment)
    except ValueError:
        return [w for w in segment.split() if w]


def update_cwd(tokens, current_cwd):
    """If the segment changes folder (cd or pushd), return the new folder."""
    if not tokens:
        return current_cwd
    cmd = os.path.basename(tokens[0]).lower()
    if cmd in ("cd", "pushd") and len(tokens) > 1:
        target = tokens[1]
        if target == "-" or target.startswith("$"):
            return current_cwd
        target = os.path.expanduser(target)
        if os.path.isabs(target):
            return os.path.normpath(target)
        if current_cwd:
            return os.path.normpath(os.path.join(current_cwd, target))
    return current_cwd


def command_start(tokens):
    """Index of the real command: past VAR=value, shell keywords, and wrappers with their flags."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in SHELL_WORDS or ASSIGNMENT.match(t):
            i += 1
            continue
        if t in WRAPPERS:
            valued = WRAPPERS[t]
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 2 if tokens[i] in valued else 1
            if t == "timeout" and i < len(tokens):
                i += 1  # the duration
            continue
        break
    return i


def is_docker_command(tokens):
    """True if the command is docker or docker-compose. Docker run, exec and compose are where npm
    belongs, and no other docker subcommand is a host npm."""
    i = command_start(tokens)
    if i >= len(tokens):
        return False
    base = re.sub(r"\.exe$", "", os.path.basename(tokens[i])).lower()
    return base in ("docker", "docker-compose")


def shell_inner_command(args):
    """The string a shell is told to run with -c, -lc, -ec and the like, or None."""
    for idx, a in enumerate(args):
        if a == "--":
            break
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) and idx + 1 < len(args):
            return args[idx + 1]
    return None


def check_segment(tokens, current_cwd, exemptions):
    """Check one command segment. Returns a refusal reason or None."""
    if not tokens:
        return None
    i = command_start(tokens)
    if i >= len(tokens):
        return None

    base_cmd = re.sub(r"\.(exe|cmd)$", "", os.path.basename(tokens[i])).lower()
    args = tokens[i + 1:]
    lowered = [a.lower() for a in args]

    # A shell told to run a string: check the string itself.
    if base_cmd in SHELL_INTERPRETERS:
        inner = shell_inner_command(args)
        if inner is not None:
            return check_command_text(inner, current_cwd, exemptions)
        return None

    # find ... -exec CMD ... ; runs CMD
    if base_cmd == "find":
        for k, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                tail = [x for x in args[k + 1:] if x not in (";", "\\;", "+", "\\")]
                return check_segment(tail, current_cwd, exemptions)
        return None

    if is_exempt(current_cwd, exemptions):
        return None

    flag_only = bool(lowered) and all(a in ALLOWED_FLAG_ONLY for a in lowered)

    if base_cmd in ("npm", "pnpm", "yarn", "bun"):
        subcmd = None
        j = 0
        while j < len(args):
            a = args[j]
            if a.startswith("-"):
                j += 2 if a in VALUE_FLAGS else 1
                continue
            subcmd = a.lower()
            break
        if subcmd in TARGET_SUBCOMMANDS:
            return REFUSAL_MESSAGE
        if base_cmd in SCRIPT_RUNNERS:
            if subcmd is None:
                # bare yarn installs; bare pnpm and bun only print help
                if base_cmd == "yarn" and not flag_only:
                    return REFUSAL_MESSAGE
            elif subcmd not in READONLY_SUBCOMMANDS:
                return REFUSAL_MESSAGE
        return None

    if base_cmd in ("npx", "pnpx", "bunx"):
        return None if flag_only else REFUSAL_MESSAGE

    if base_cmd in STANDALONE_REFUSE:
        return None if flag_only else REFUSAL_MESSAGE

    if base_cmd == "expo" and "start" in lowered:
        return REFUSAL_MESSAGE

    return None


def strip_heredocs(command):
    """Cut heredoc bodies out of a command line.

    Returns (text, heredocs). text is the command with every body and its closing line removed (the
    line carrying << stays, so the command that reads the body is still checked). heredocs is a list
    of (body, quoted, line): the body text, whether its delimiter was quoted (a quoted delimiter means
    the shell does no expansion in it), and the command line that carried the operator. A body with
    no closing line runs to the end, as the shell reads it."""
    heredocs = []
    text = command
    scan = 0
    while True:
        plain = QUOTED.sub(lambda m: "Q" * len(m.group(0)), text)
        m = HEREDOC_OP.search(plain, scan)
        if not m:
            break
        nl = plain.find("\n", m.end())
        if nl == -1:
            break  # no body follows
        line_start = plain.rfind("\n", 0, m.start()) + 1
        line = text[line_start:nl]
        pos = nl + 1
        for op in HEREDOC_OP.finditer(plain, m.start(), nl):
            raw = text[op.start(2):op.end(2)]
            quoted = raw[0] in "'\"\\"
            delim = raw.strip("'\"\\")
            dash = op.group(1) == "-"
            body_lines = []
            end = len(text)
            cur = pos
            while cur < len(text):
                eol = text.find("\n", cur)
                eol = len(text) if eol == -1 else eol
                row = text[cur:eol]
                if (row.lstrip("\t") if dash else row) == delim:
                    end = min(eol + 1, len(text))
                    break
                body_lines.append(row)
                cur = eol + 1
            else:
                end = len(text)
            heredocs.append(("\n".join(body_lines), quoted, line))
            pos = end
        text = text[:nl + 1] + text[pos:]
        scan = nl + 1
    return text, heredocs


def fed_to_shell(line):
    """True if a command line pipes or redirects into a shell reading its script from stdin
    (bash <<EOF, cat <<EOF | sh -s). A shell given -c or a script file reads something else."""
    plain = QUOTED.sub(lambda m: "Q" * len(m.group(0)), line)
    start = 0
    segs = []
    for m in SEPARATOR.finditer(plain):
        segs.append(line[start:m.start()])
        start = m.end()
    segs.append(line[start:])
    for seg in segs:
        tokens = get_tokens(seg)
        if not tokens or is_docker_command(tokens):
            continue
        i = command_start(tokens)
        if i >= len(tokens):
            continue
        base = re.sub(r"\.(exe|cmd)$", "", os.path.basename(tokens[i])).lower()
        if base not in SHELL_INTERPRETERS:
            continue
        args = [a for a in tokens[i + 1:] if not a.startswith("<<")]
        if shell_inner_command(args) is not None:
            continue
        if all(a.startswith("-") for a in args):
            return True
    return False


def check_command_text(command, cwd, exemptions):
    """Check a command line for host runs. Returns a refusal reason or None."""
    if not command or not isinstance(command, str):
        return None

    # A heredoc body is text, not commands. Check it only where the shell would act on it.
    command, heredocs = strip_heredocs(command)
    for body, quoted, line in heredocs:
        if fed_to_shell(line):
            reason = check_command_text(body, cwd, exemptions)
            if reason:
                return reason
        elif not quoted:
            for m in HEREDOC_SUBSTITUTION.finditer(body):
                sub_cmd = m.group(1) if m.group(1) is not None else m.group(2)
                if sub_cmd and sub_cmd.strip():
                    reason = check_command_text(sub_cmd, cwd, exemptions)
                    if reason:
                        return reason

    # $( ) and backticks run even inside double quotes, but never inside single quotes.
    unsingled = SINGLE_QUOTED.sub(lambda m: " " * len(m.group(0)), command)
    for m in SUBSTITUTION.finditer(unsingled):
        sub_cmd = m.group(1) if m.group(1) is not None else m.group(2)
        if sub_cmd and sub_cmd.strip():
            sub_reason = check_command_text(sub_cmd, cwd, exemptions)
            if sub_reason:
                return sub_reason

    # Split into segments at the separators outside quotes.
    plain = QUOTED.sub(lambda m: "Q" * len(m.group(0)), command)
    segments = []
    last = 0
    for m in SEPARATOR.finditer(plain):
        part = command[last:m.start()].strip()
        if part:
            segments.append(part)
        last = m.end()
    tail = command[last:].strip()
    if tail:
        segments.append(tail)

    current_cwd = cwd
    for seg in segments:
        tokens = get_tokens(seg)
        if not tokens:
            continue
        current_cwd = update_cwd(tokens, current_cwd)
        if is_docker_command(tokens):
            continue
        reason = check_segment(tokens, current_cwd, exemptions)
        if reason:
            return reason
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0

    try:
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            cwd = os.getcwd()
        reason = check_command_text(command, cwd, read_exemptions())
    except Exception as e:
        print(f"docker-first-guard: allowed after an error ({type(e).__name__})", file=sys.stderr)
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
