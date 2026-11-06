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
Allowed: the address in grep, echo, git, local files, and the mail tools (gmail_api.py, calendar_api.py).
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
MAX_SCRIPT_BYTES = 512 * 1024
INLINE_FLAGS = ("-c", "-e", "-E", "-p", "-r", "-m", "--eval", "--print")

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


def script_path(token, cwd):
    token = token.strip("'\"")
    if not token or token.startswith(("-", "<", "$", "(")):
        return None
    p = os.path.expanduser(token)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return p if os.path.isfile(p) else None


def sinks(cmd, cwd, allowed):
    """Names of network sinks in the command. A script file is judged by its own text."""
    found = []
    if CLIENT_RE.search(cmd):
        found.append("network client")
    for m in INTERP_RE.finditer(cmd):
        toks = m.group("rest").split()
        i, inline = 0, False
        while i < len(toks) and toks[i].startswith("-"):
            if toks[i] in INLINE_FLAGS:
                inline = True
            i += 1
        if inline or i >= len(toks) or toks[i].startswith(("<", "-")):
            if NETWORK_CODE.search(cmd):
                found.append("inline code that uses the network")
            continue
        path = script_path(toks[i], cwd)
        if path is None:
            if NETWORK_CODE.search(cmd):
                found.append("code that uses the network")
            continue
        if os.path.basename(path) in allowed:
            continue
        try:
            with open(path, errors="replace") as f:
                body = f.read(MAX_SCRIPT_BYTES)
        except OSError:
            continue
        if NETWORK_CODE.search(body):
            found.append(("script file", body))
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
