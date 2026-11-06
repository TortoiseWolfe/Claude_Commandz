#!/usr/bin/env python3
"""PreToolUse hook: refuse a web call that would send the account owner's email address to a website.

Why: anthropics/claude-code#107757 reports the signed-in account's email address going out in User-Agent headers to
third-party sites. Nothing in the harness stops a command from doing the same on purpose or by accident, so this hook refuses
any Bash, WebFetch or WebSearch call that would carry the address to a server.

stdin: {"tool_name": ..., "tool_input": {...}, "cwd": ...}
deny: reason on stderr, exit 2.  allow: exit 0.  An internal error (unreadable payload, no address known) allows and logs,
so a missing file never blocks all work. The reason never echoes the address.

The address is never written in this repo. It comes from
  - ~/.config/afa/identity-guard.json   {"addresses": ["a@b.c"], "allow_scripts": ["my_mail_tool.py"]}   (private; optional)
  - ~/.claude.json  oauthAccount.emailAddress   (the address Claude Code itself holds)
Tests point both at temporary files with IDENTITY_GUARD_CONFIG and IDENTITY_GUARD_CLAUDE_JSON.

What counts as sending (Bash): the address appears anywhere in the command AND the command holds a network sink:
  - curl, wget, httpie (http, https), xh, nc and friends in command position (also after sudo, env, time, xargs, ...);
  - python, node, ruby, perl or php in command position running inline code (-c, -e, a heredoc) or a script file that
    uses a network library. A script file is read, so a script an agent wrote earlier is judged by what it contains.
  That covers a header (-H, -A, --user-agent), the URL (plain or percent-encoded), and a body (-d, a pipe into -d @-).
  Reading the address from git config or an environment variable into a sink counts as well.
  A script this hook can't find, can't read or can't read in full counts as a sink; it is never assumed safe. Every token
  after the interpreter is tried as a file, in the working folder and in any folder a cd in the line moves to.
Allowed: the address in grep, echo, git, local files, and the mail tools gmail_api.py and calendar_api.py (plus names under
  allow_scripts). A mail tool is allowed by its real path in ~/.claude/scripts, never by file name alone, so a copy or a link
  with the same name somewhere else is judged like any other script.
Known gaps: an address assembled at run time from pieces, an address held in a variable set elsewhere, and a sink reached
through ssh, docker exec or a language this hook does not list.
"""
import html
import json
import os
import re
import sys
import time
import urllib.parse

CONFIG = "~/.config/afa/identity-guard.json"
CLAUDE_JSON = "~/.claude.json"
LOG = "~/.claude/state/identity-guard.log"
DEFAULT_ALLOWED_SCRIPTS = {"gmail_api.py", "calendar_api.py"}
MAX_SCRIPT_BYTES = 4 * 1024 * 1024
SCRIPTS_DIR = "~/.claude/scripts"
NOT_A_FILE = ("-", "<", "$", "(", ">", "|", ";", "&")
SEPARATOR = re.compile(r"\|\||&&|[;|&]")
# Settings that make an interpreter load other code, and the env wrapper that sets them, so a mail tool is not trusted beside them.
INTERPRETER_ENV = re.compile(r"\b(?:PYTHON\w*|NODE_\w+|RUBY\w*|PERL5?\w*|BUN_\w+|DENO_\w+|LD_\w+|DYLD_\w+)\b|(?:^|[\s;&|(])(?:env|source|export|declare|typeset)\s|(?:^|[;&|(]\s*)\.\s")
CHANGES_FOLDER = re.compile(r"\b(?:cd|pushd|popd|chdir)\b")
INLINE_FLAGS = ("-c", "-e", "-E", "-p", "-r", "-m", "--eval", "--print")
INLINE_PREFIXES = ("-c", "-m", "-e", "-E", "-p", "-r", "--eval", "--print", "--require", "--import", "--loader", "--input-type")

WHY = (
    "This call would send the account owner's email address to a website. Claude Code has been reported putting that "
    "address in User-Agent headers (anthropics/claude-code#107757), so the hub refuses it in a header, a URL or a body. "
    "Take the address out of the request. For mail, use python3 ~/.claude/scripts/gmail_api.py, which is allowed."
)

NET_CLIENTS = ("curl", "wget", "http", "https", "httpie", "xh", "nc", "ncat", "netcat", "socat", "telnet", "aria2c",
               "lynx", "w3m", "links")
INTERPRETERS = r"(?:python[\d.]*|node(?:js)?|deno|bun|ruby|perl|php)"
WRAPPERS = r"(?:sudo|doas|env|time|nice|nohup|xargs|exec|command|builtin|timeout|stdbuf|setsid|unbuffer)"
START = r"(?:^|[;&|(`\n{\"']|\$\(|\b(?:then|do|else|elif|if|while)\s)\s*"
PREFIX = (r"(?:\w+=\S*\s+)*(?:" + WRAPPERS + r"\s+(?:(?:-\S+|\d+[smhd]?|\w+=\S*)\s+)*)*")
CLIENT_RE = re.compile(START + PREFIX + r"(?:\S*/)?(?:" + "|".join(NET_CLIENTS) + r")(?=\s|$)", re.M)
INTERP_RE = re.compile(START + PREFIX + r"(?:\S*/)?(?P<name>" + INTERPRETERS + r")(?=\s|$)(?P<rest>[^\n]*)", re.M)

# Code that can reach the network. A bare URL in a comment does not count; a library or a fetch call does.
NETWORK_CODE = re.compile(
    r"\bimport\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*(?:requests|urllib\d?|httpx|aiohttp|socket|http\.client|pycurl|websockets?)\b"
    r"|\bfrom\s+(?:urllib\d?|http|requests|httpx|aiohttp)\b"
    r"|\burllib\d?\.|\brequests\.(?:get|post|put|head|patch|delete|request|Session)\b|\bhttpx\.|\baiohttp\."
    r"|\bhttp\.client\b|\bsocket\.(?:socket|create_connection)\b"
    r"|\bfetch\s*\(|\baxios\b|\bXMLHttpRequest\b|\bundici\b|\bnode-fetch\b"
    r"|require\(\s*['\"](?:node:)?(?:https?|net|tls|axios|got|undici|node-fetch)['\"]\s*\)"
    r"|from\s+['\"](?:node:)?(?:https?|axios|got|undici|node-fetch)['\"]"
    r"|Net::HTTP|net/http|open-uri|URI\.open|LWP|HTTP::Tiny|curl_init|file_get_contents\(\s*['\"]https?"
    r"|\b(?:curl|wget)\s+-|\bnew\s+WebSocket\b",
    re.I,
)
# Reading the account address from where it lives, straight into a command.
DYNAMIC_ADDRESS = re.compile(
    r"git\s+config\b[^\n;|&]*\buser\.email\b|\$\{?(?:GIT_AUTHOR_EMAIL|GIT_COMMITTER_EMAIL|EMAIL)\b"
    r"|\boauthAccount\b|\.emailAddress\b", re.I)


def log(msg):
    try:
        path = os.environ.get("IDENTITY_GUARD_LOG") or os.path.expanduser(LOG)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def read_json(path):
    try:
        with open(os.path.expanduser(path)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings():
    """(addresses, allowed script names). Addresses are lower-cased."""
    cfg = read_json(os.environ.get("IDENTITY_GUARD_CONFIG") or CONFIG)
    addrs = {a.strip().lower() for a in cfg.get("addresses", []) if isinstance(a, str) and "@" in a}
    account = read_json(os.environ.get("IDENTITY_GUARD_CLAUDE_JSON") or CLAUDE_JSON).get("oauthAccount")
    if isinstance(account, dict) and isinstance(account.get("emailAddress"), str) and "@" in account["emailAddress"]:
        addrs.add(account["emailAddress"].strip().lower())
    allowed = set(DEFAULT_ALLOWED_SCRIPTS)
    allowed.update(s for s in cfg.get("allow_scripts", []) if isinstance(s, str))
    return addrs, allowed


def views(text):
    """The text as written, decoded (percent, HTML entity, \\u and \\x escapes), and with quotes and backslashes removed."""
    out = [text]
    dec = text
    for _ in range(2):
        dec = urllib.parse.unquote(dec)
    dec = html.unescape(dec)
    dec = re.sub(r"\\u00([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), dec)
    dec = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), dec)
    out.append(dec)
    out.append(re.sub(r"['\"\\]", "", dec))
    return [v.lower() for v in out]


def has_address(text, addrs):
    for v in views(text):
        for a in addrs:
            if re.search(r"(?<![\w.+-])" + re.escape(a) + r"(?![\w-]|\.\w)", v):
                return True
    return False


def scripts_dir():
    return os.path.realpath(os.path.expanduser(os.environ.get("IDENTITY_GUARD_SCRIPTS_DIR") or SCRIPTS_DIR))


def is_allowed_tool(path, allowed):
    """A mail tool is allowed by where it really lives (the installed scripts folder), never by its file name alone."""
    real = os.path.realpath(path)
    return any(real == os.path.join(scripts_dir(), name) for name in allowed)


def cd_targets(cmd):
    return re.findall(r"\bcd\s+([^\s;&|]+)", cmd)


def cd_unfollowable(cmd):
    """True when the line changes folder in a way this hook can't work out: pushd, popd, cd - or a target built at run time."""
    if re.search(r"\b(?:pushd|popd)\b", cmd):
        return True
    return any(d.strip("'\"") == "-" or re.search(r"[$`*?]", d) for d in cd_targets(cmd))


def candidate_files(tokens, cwd, cmd):
    """(token, path) for every token after an interpreter that names an existing file. Looks in the working folder and in
    any folder the command cd's into, so an option that takes a value (python -W ignore x.py) or a cd earlier in the line
    hides nothing."""
    dirs = [cwd]
    for d in cd_targets(cmd):
        d = os.path.expanduser(d.strip("'\""))
        dirs.append(d if os.path.isabs(d) else os.path.join(cwd, d))
    out = []
    for tok in tokens:
        tok = tok.strip("'\"")
        if not tok or tok.startswith(NOT_A_FILE):
            continue
        p = os.path.expanduser(tok)
        for base in ([""] if os.path.isabs(p) else dirs):
            q = os.path.join(base, p) if base else p
            if os.path.isfile(q) and (tok, q) not in out:
                out.append((tok, q))
    return out


def sinks(cmd, cwd, allowed):
    """Network sinks in the command: names as strings, or ("script file", text) for a script that uses the network.
    A script is judged by its own text. One that can't be found, read in full or told apart from a mail tool counts as a sink.
    Only the words up to the next shell separator belong to the interpreter. A mail tool is skipped only when it is the
    script itself (the very next word after the interpreter, named by an absolute path) and nothing in the line can change what
    the interpreter loads or where it looks."""
    found = []
    if CLIENT_RE.search(cmd):
        found.append("network client")
    # A mail tool is trusted only when nothing in the line can change what runs: no folder change, no interpreter settings.
    hijackable = bool(INTERPRETER_ENV.search(cmd) or CHANGES_FOLDER.search(cmd))
    for m in INTERP_RE.finditer(cmd):
        toks = SEPARATOR.split(m.group("rest"))[0].split()
        words = [t.strip("'\"") for t in toks if not t.startswith(NOT_A_FILE)]
        inline = any(t.startswith(INLINE_PREFIXES) for t in toks) or not words
        if inline and NETWORK_CODE.search(cmd):
            found.append("inline code that uses the network")
        if any(t.startswith("-m") for t in toks):
            found.append("a module this hook can't read")
        if cd_unfollowable(cmd):
            found.append("a folder change this hook can't follow")
        files = candidate_files(toks, cwd, cmd)
        if not files:
            if not inline:
                found.append("a script this hook can't find")
            continue
        first = words[0] if words and not inline else None
        for tok, path in files:
            if tok == first and toks and toks[0].strip("'\"") == first and os.path.isabs(os.path.expanduser(first)) \
                    and not hijackable and is_allowed_tool(path, allowed):
                continue
            try:
                with open(path, errors="replace") as f:
                    body = f.read(MAX_SCRIPT_BYTES + 1)
            except OSError:
                found.append("a script this hook can't read")
                continue
            if len(body) > MAX_SCRIPT_BYTES:
                found.append("a script too large to read in full")
            elif NETWORK_CODE.search(body):
                found.append(("script file", body))
        if first is not None and not any(t == first for t, _ in files):
            found.append("a script this hook can't find")
    return found


def check_bash(cmd, cwd, addrs, allowed):
    found = sinks(cmd, cwd, allowed)
    if not found:
        return None
    if has_address(cmd, addrs) or DYNAMIC_ADDRESS.search(cmd):
        return "a Bash call carries the address to a network sink"
    for f in found:
        if isinstance(f, tuple) and has_address(f[1], addrs):
            return "a script this call runs holds the address and uses the network"
    return None


def check(payload, addrs=None, allowed=None):
    """Return a deny reason, or None to allow."""
    if addrs is None:
        addrs, allowed = load_settings()
    allowed = allowed if allowed is not None else set(DEFAULT_ALLOWED_SCRIPTS)
    if not addrs:
        return None
    tool = payload.get("tool_name", "")
    args = payload.get("tool_input") or {}
    if tool == "Bash":
        cmd = args.get("command")
        if not isinstance(cmd, str):
            return None
        why = check_bash(cmd, payload.get("cwd") or os.getcwd(), addrs, allowed)
    elif tool == "WebFetch":
        why = "a WebFetch URL carries the address" if has_address(str(args.get("url", "")), addrs) else None
    elif tool == "WebSearch":
        why = "a WebSearch query carries the address" if has_address(str(args.get("query", "")), addrs) else None
    else:
        return None
    return "%s. %s" % (why, WHY) if why else None


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        addrs, allowed = load_settings()
        if not addrs:
            log("no address known (no %s, no account in %s); guard is not checking" % (CONFIG, CLAUDE_JSON))
            return 0
        reason = check(payload, addrs, allowed)
    except Exception as e:  # internal error: allow, log, never block all work
        log("internal error: %s" % e)
        return 0
    if reason:
        print("BLOCKED: " + reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
