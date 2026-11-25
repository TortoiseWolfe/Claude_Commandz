#!/usr/bin/env python3
"""PreToolUse hook: refuse a web call that would send the account owner's email address to a website.

Why: anthropics/claude-code#107757 reports the signed-in account's email address going out in User-Agent headers to
third-party sites. Nothing in the harness stops a command from doing the same on purpose or by accident, so this hook refuses
any Bash, WebFetch or WebSearch call, and any MCP tool call (browser_navigate, transcript tools, code run in the browser),
that would carry the address to a server.

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
Binary attachments for a mail tool are data: when the program is a mail tool the hook trusts (the plain form above), a binary file named
  after it (a zip, pdf, image or audio attachment) is not scanned as code, however large. Binary means a null byte in the first 8 KB, or
  the magic bytes of a non-text format (zip, png, jpg, gzip, 7z, rar, xz), or an ASCII magic (%PDF-, GIF8, ID3, RIFF, OggS, fLaC, BZh)
  followed by bytes that are not text. Any other program (a script of an agent's own, an interpreter at an odd path, a line that cd's or
  sets interpreter variables) reads every file it names as before, so a big binary there still blocks, and a text file is always read.
Allowed: the address in grep, echo, git, local files, and the mail tools gmail_api.py and calendar_api.py (plus names under
  allow_scripts). A mail tool is allowed by its real path in ~/.claude/scripts, never by file name alone, so a copy or a link
  with the same name somewhere else is judged like any other script.
Known gaps: an address assembled at run time from pieces, an address held in a variable set elsewhere, and a sink reached
through ssh, docker exec or a language this hook does not list.
This is a guard against accidents and plain mistakes, not a sandbox: a line written on purpose to defeat a text reading of the
command can still win. A mail tool is trusted only in its plainest form, so anything odd falls to the stricter script judging.
"""
import codecs
import html
import json
import os
import re
import shlex
import stat
import sys
import time
import urllib.parse

CONFIG = "~/.config/afa/identity-guard.json"
CLAUDE_JSON = "~/.claude.json"
LOG = "~/.claude/state/identity-guard.log"
APPROVED_HOSTS = "~/.config/afa/identity-guard-hosts.txt"
HOST_NAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
APPROVED_URL = re.compile(r"https://([a-z0-9.-]+)(?::\d+)?(?:[/?#]\S*)?", re.I)  # no login in front of the host, no other scheme
# The only curl options an approved send may use (an allow list: any other option, long or short, refuses the whole command). Body options
# carry the address; value options carry a word of their own that must not hold it. A long option written with `=` is refused.
BODY_OPTIONS = {"-d", "-F", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--form", "--form-string", "--json"}
FLAG_OPTIONS = {"-s", "-S", "-f", "-i", "--silent", "--show-error", "--fail", "--include", "--http1.1", "--http2"}
_HEADER_VALUE = r"(Content-Type|Accept): [\w/+.;= -]{1,80}"
VALUE_OPTIONS = {"-X": r"(GET|POST|PUT|PATCH)", "--request": r"(GET|POST|PUT|PATCH)", "-m": r"\d{1,4}", "--max-time": r"\d{1,4}",
                 "--connect-timeout": r"\d{1,4}", "-H": _HEADER_VALUE, "--header": _HEADER_VALUE}
DEFAULT_ALLOWED_SCRIPTS = {"gmail_api.py", "calendar_api.py"}
MAX_SCRIPT_BYTES = 4 * 1024 * 1024
SCRIPTS_DIR = "~/.claude/scripts"
BINARY_SNIFF_BYTES = 8192
BINARY_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"\x89PNG", b"\xff\xd8\xff", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!", b"\xfd7zXZ")
BINARY_TEXTY_MAGIC = (b"%PDF-", b"GIF8", b"ID3", b"RIFF", b"OggS", b"fLaC", b"BZh")  # these start with text, so the rest must not be text too
NOT_A_FILE = ("-", "<", "$", "(", ">", "|", ";", "&")
SEPARATOR = re.compile(r"\|\||&&|[;|&]")
# Settings that make an interpreter load other code, and the env wrapper that sets them, so a mail tool is not trusted beside them.
INTERPRETER_ENV = re.compile(r"\b(?:PYTHON\w*|NODE_\w+|RUBY\w*|PERL5?\w*|BUN_\w+|DENO_\w+|LD_\w+|DYLD_\w+)\b|(?:^|[\s;&|(])(?:env|source|export|declare|typeset)\s|(?:^|[;&|(]\s*)\.\s")
# Anything that can make the word "python3" mean something else on the same line: PATH changes, aliases, functions, hash.
SHADOWING = re.compile(r"\bPATH\b|\b(?:alias|hash|enable|function|command_not_found_handle)\b|\w+\s*\(\s*\)\s*\{")
SYSTEM_INTERPRETER_DIRS = ("/usr/bin/", "/usr/local/bin/", "/bin/", "/opt/homebrew/bin/")
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
INTERP_RE = re.compile(START + PREFIX + r"(?P<dir>\S*/)?(?P<name>" + INTERPRETERS + r")(?=\s|$)(?P<rest>[^\n]*)", re.M)

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
# A script that starts other programs can reach the network through them, so for the data-file rule it counts as network code.
STARTS_PROGRAMS = re.compile(
    r"\bsubprocess\b|\bos\.(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*)\b|\bPopen\b|\bpty\b|\bctypes\b|\bpexpect\b"
    r"|\b(?:eval|exec|compile|__import__)\s*\(|\bimportlib\b|\brunpy\b|\bchild_process\b|\bspawn(?:Sync)?\s*\("
    r"|\bexecFile(?:Sync)?\b|\bexecSync\b|\bsystem\s*\(|\bpopen\b|`|%x[\[({]|\bshell_exec\b|\bpassthru\b|\bproc_open\b"
    # a script that can load a file it is handed as code (or reach a program through a loader) is not trusted with data files
    r"|\brequire\b|\bimport\s*\(|\bsys\.path\b|\bexec[lv]p?e?\b|\bpickle\b|\bmarshal\b|\bload_(?:source|module)\b|\bdlopen\b"
    r"|\bwebbrowser\b|\bsource\b|\bdo\s*[\"'$(]|\bvm\b|\bFunction\s*\(|\binclude(?:_once)?\b|\brequire_once\b|\b(?:instance|class)_eval\b",
    re.I,
)
# A file with one of these endings is code an interpreter can load, so the data-file rule never skips it.
SOURCE_SUFFIXES = (".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".pm", ".php", ".sh", ".bash", ".zsh", ".lua", ".tcl")
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


def is_binary(path):
    """True for a file that is data, not a script: a null byte in its first 8 KB, a non-text magic, or a text-looking magic followed by non-text."""
    try:
        with open(path, "rb") as f:
            head = f.read(BINARY_SNIFF_BYTES)
    except OSError:
        return False
    if b"\0" in head or head.startswith(BINARY_MAGIC):
        return True
    if head.startswith(BINARY_TEXTY_MAGIC):
        try:
            codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        except UnicodeDecodeError:
            return True
    return False


def starts_with_shebang(path):
    """True when the file's first two bytes are #!, whatever it is called: a program, not data."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return True


def script_is_quiet(path):
    """True when the script's own text can be read in full and holds no network code and starts no other program."""
    try:
        with open(path, errors="replace") as f:
            body = f.read(MAX_SCRIPT_BYTES + 1)
    except OSError:
        return False
    return len(body) <= MAX_SCRIPT_BYTES and not NETWORK_CODE.search(body) and not STARTS_PROGRAMS.search(body)


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
    hijackable = bool(INTERPRETER_ENV.search(cmd) or CHANGES_FOLDER.search(cmd) or SHADOWING.search(cmd))
    for m in INTERP_RE.finditer(cmd):
        # The interpreter itself must be a bare name or a system path; one at any other path is a program like any other.
        odd_interpreter = bool(m.group("dir")) and m.group("dir") not in SYSTEM_INTERPRETER_DIRS
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
        plain_form = first is not None and not odd_interpreter and toks and toks[0].strip("'\"") == first \
            and os.path.isabs(os.path.expanduser(first)) and not hijackable
        trusted = False  # the program is a mail tool in its plainest form: the files it is given are its data
        for tok, path in files:
            if tok == first and plain_form and is_allowed_tool(path, allowed):
                trusted = True
                break
        # A script with no network code, started in its plainest form: the other files it is given are its data, not code.
        data_mode = False
        if plain_form and not trusted:
            own = [p for t, p in files if t == first]
            data_mode = bool(own) and all(script_is_quiet(p) for p in own)
        for tok, path in files:
            if tok == first and plain_form and is_allowed_tool(path, allowed):
                continue
            if data_mode and tok != first and not path.lower().endswith(SOURCE_SUFFIXES) and not starts_with_shebang(path):
                continue
            if trusted and tok != first and is_binary(path):  # an attachment for a mail tool: data, not code, so not scanned
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
                found.append(("script file", body, path))
        if first is not None and not any(t == first for t, _ in files):
            found.append("a script this hook can't find")
    return found


def load_approved_hosts():
    """The hosts the account owner has approved his address going to (issue 167): one host a line in a private file,
    `host  "his quoted word"  YYYY-MM-DD`. A line missing the quoted word or the date counts for nothing. A file that is not his own regular
    file, or that anyone else can read or write (mode must be 600), a missing file and a bad file all give no host, so the guard
    refuses as it always has."""
    path = os.path.expanduser(os.environ.get("IDENTITY_GUARD_APPROVED_HOSTS") or APPROVED_HOSTS)
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
            log("approved-hosts file ignored: it must be his own regular file with mode 600")
            return frozenset()
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return frozenset()
    hosts = set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split(None, 1)[0].lower()
        if HOST_NAME.fullmatch(word) and re.search(r'"[^"]+"', line) and re.search(r"\b\d{4}-\d{2}-\d{2}\b", line):
            hosts.add(word)
    return frozenset(hosts)


def body_only_to_approved_host(cmd, addrs, hosts):
    """True only for one plain curl call that puts the address in a form field or request body and sends it to an approved host.
    Everything else (a header, the user agent, a login, a cookie, the URL, a proxy, a redirect, a second program, a pipe, a variable or
    substitution, a host not on the list, a word this reading can't place) gives False and the guard refuses as before."""
    if not hosts:
        return False
    if "#" in cmd or "\\" in cmd:
        return False  # shlex and the shell disagree on where a comment starts, and on what a backslash does inside a word
    quote = ""
    for ch in cmd:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "{}*?[]~":
            return False  # the shell turns an unquoted brace, star, question mark, bracket or tilde into other words; shlex leaves one word
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        toks = list(lex)
    except ValueError:
        return False
    if not toks or os.path.basename(toks[0]) != "curl":
        return False
    if any(t and set(t) <= set("();<>|&") for t in toks) or any("$" in t or "`" in t or "\n" in t for t in toks):
        return False  # a second command, a pipe, a redirect, or a word made at run time
    if len(toks) < 2 or toks[1] not in ("-q", "--disable"):
        return False  # without it curl reads ~/.curlrc, which could set a proxy or another web address; a session could have written it
    urls, pend = [], None  # pend: the option whose value is the next word
    for t in toks[2:]:
        if pend is not None:
            if pend not in BODY_OPTIONS and (has_address(t, addrs) or not re.fullmatch(VALUE_OPTIONS[pend], t)):
                return False  # only a body option's value may hold the address; a header, a login, a cookie or a referer never
            pend = None
            continue
        attached = len(t) > 2 and t[:2] in ("-d", "-F") and t[2] != "-"  # the value is stuck to the option: -dfield=value
        if has_address(t, addrs) and not attached:
            return False  # the address in a web address, or as an option's own word
        if t in BODY_OPTIONS or t in VALUE_OPTIONS:
            pend = t
        elif t in FLAG_OPTIONS or attached or re.fullmatch(r"-[sSfi]{2,}", t):
            continue
        elif t.startswith("-"):
            return False  # an option this reading does not know: a redirect, a proxy, -G, a name override, a settings file, an upload
        else:
            urls.append(t)
    if pend is not None:
        return False
    if not urls:
        return False
    for u in urls:
        m = APPROVED_URL.fullmatch(u)
        if not m or m.group(1).lower() not in hosts:
            return False
    return True


def check_bash(cmd, cwd, addrs, allowed, hosts=frozenset()):
    found = sinks(cmd, cwd, allowed)
    if not found:
        return None
    if hosts and found == ["network client"] and not DYNAMIC_ADDRESS.search(cmd) and body_only_to_approved_host(cmd, addrs, hosts):
        return None
    if has_address(cmd, addrs) or DYNAMIC_ADDRESS.search(cmd):
        return "a Bash call carries the address to a network sink"
    for f in found:
        if isinstance(f, tuple) and has_address(f[1], addrs):
            return "the file %s holds the address and uses the network" % (f[2] if len(f) > 2 else "a script this call runs")
    return None


# MCP tools (the browser, video transcripts, search) reach websites with whatever they are given. A field that holds a web
# address or a search is judged like a WebFetch URL; a field that holds code is refused when it holds the address at all.
MCP_URL_KEYS = {"url", "uri", "href", "link", "src", "query", "q", "search", "term", "address", "endpoint", "videoid", "video_id"}
MCP_CODE_KEYS = {"code", "script", "function", "expression", "js", "javascript"}


def mcp_strings(value, key="", depth=0):
    """Yield (lower-cased key, string) for every string in a tool input, however deeply nested."""
    if depth > 8:  # too deep to walk: judge the whole thing as one string instead of skipping it
        yield "url", json.dumps(value, default=str)  # a deep value is treated like a URL: refused when it holds the address
        return
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from mcp_strings(v, str(k).lower(), depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from mcp_strings(v, key, depth + 1)


def check_mcp(tool, args, addrs):
    short = tool.split("__")[-1]
    for key, text in mcp_strings(args):
        if not has_address(text, addrs):
            continue
        if key in MCP_URL_KEYS or "url" in key:
            return "an MCP %s call carries the address in its %s" % (short, key)
        if key in MCP_CODE_KEYS:  # any code that holds the address: it could send it by a route this list does not name
            return "an MCP %s call runs code that would send the address to a website" % short
    return None


def check(payload, addrs=None, allowed=None, hosts=None):
    """Return a deny reason, or None to allow. hosts: the approved hosts (None reads the private file)."""
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
        why = check_bash(cmd, payload.get("cwd") or os.getcwd(), addrs, allowed, load_approved_hosts() if hosts is None else hosts)
    elif tool == "WebFetch":
        why = "a WebFetch URL carries the address" if has_address(str(args.get("url", "")), addrs) else None
    elif tool == "WebSearch":
        why = "a WebSearch query carries the address" if has_address(str(args.get("query", "")), addrs) else None
    elif isinstance(tool, str) and tool.startswith("mcp__"):
        why = check_mcp(tool, args, addrs)
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
