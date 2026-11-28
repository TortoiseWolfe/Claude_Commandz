#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block literal secrets and credential-file dumps.

stdin: {"tool_name": ..., "tool_input": {...}}
block: reason on stderr, exit 2.  allow: exit 0.  internal error: allow + log.
The reason never echoes a matched secret.
"""
import fnmatch
import glob
import importlib.util
import json
import os
import re
import shlex
import stat
import sys
import time
import unicodedata

WORD = (r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))"
        r"|credential|private[_-]?key|client[_-]?secret|session[_-]?(?:id|key|token|secret|cookie)|cookie|bearer|webhook)")
PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKENS = [
    ("GitHub token", r"gh[opsu]_\w{20,}"), ("GitHub PAT", r"github_pat_\w{20,}"),
    ("API key (sk-)", r"sk-[A-Za-z0-9_-]{20,}"), ("Stripe key", r"sk_(?:live|test)_\w{10,}"),
    ("AWS key id", r"AKIA[0-9A-Z]{16}"), ("AWS key id", r"ASIA[0-9A-Z]{16}"),
    ("Google API key", r"AIza[\w-]{30,}"), ("Slack token", r"xox[abprs]-[\w-]{10,}"),
    ("GitLab token", r"glpat-[\w-]{20,}"),
    ("JWT", r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), ("private key", PEM.pattern),
    # A webhook's secret is a path segment, so no key name or token prefix gives it away.
    # A long segment after /webhook(s)/ is the token; docs links stay allowed.
    ("webhook URL", r"https?://[^\s/'\"]+(?:/[^\s/'\"]+?)*?/webhooks?/[^\s'\"]*?[\w-]{24,}"),
    ("Slack webhook", r"https?://hooks\.slack\.com/(?:services|workflows|triggers)/[\w/]{20,}"),
]
VAL = r"""(?:"[^"]*"|'[^']*'|[^\s"';&|)]+)"""
ASSIGN = re.compile(r"(?P<name>[A-Za-z0-9_.-]*" + WORD + r"[A-Za-z0-9_.-]*)=(?P<val>" + VAL + ")", re.I)
# A quote that opens a word (start, or after whitespace ( [ , { = : ; | & < >), then an optional ^, "export " or "set ", right before a name:
# the quote that follows that name's = closes this string. A quote glued to a word before it (a"NAME=") does not open one.
CLOSES_STRING = re.compile(r"""(?:^|[\s(\[,{=:;|&<>])(["'])[\^]*(?:(?:export|set)\s+)?\Z""")
GLUED = re.compile(r"""(?P<q>["'])[\^]*(?:(?:export|set)\s+)?(?P<name>[A-Za-z0-9_.-]*""" + WORD + r"""[A-Za-z0-9_.-]*)=(?P=q)(?P<val>(?![\s+.)\],;&|}:<>])""" + VAL + ")", re.I)
NAME_NOT_SECRET = re.compile(r"(?:file|path|dir|url|uri|name|type|mode|host|port|header|endpoint"
                             r"|enabled|ttl|expiry|expires)$", re.I)
FLAG_SP = re.compile(r"(?<![\w-])--(?:password|passwd|token|secret|api-?key|auth-token|access-token"
                     r"|client-secret|private-key)\s+(?P<val>" + VAL + ")", re.I)
# A URL query parameter (the same unspaced word holds ://, so a shell & does not count) whose value is a plain 7 to 10 digit number is an id, not a secret (Greenhouse's public job id in
# job-boards.greenhouse.io/embed/job_app?for=x&token=4567891). The digits must end at a non-word character, so a value that goes on into
# letters (a mixed token) still counts. A bare digit value was already allowed; this covers the same id with punctuation after it.
URL_ID = re.compile(r"\d{7,10}(?![A-Za-z0-9_-])")
PLACEHOLDER = re.compile(r"placeholder|example|dummy|changeme|redacted|xxx+|^<.*>$|your[-_]", re.I)

SENSITIVE = [
    ".claude.json", ".claude/settings.json", ".claude/settings.local.json", "api-key", "api-token",
    "hosts.yml", "auth.json", "credentials", ".pem", ".p8", "ssh private key", "gateways.json",
    "mcp-token.txt", ".aws/", ".netrc", ".npmrc", ".pgpass", ".docker/config.json", "gcloud/*.json",
]
SENS_RES = [
    r"\.claude\.json(?![\w-])", r"\.claude/settings\.json(?![\w-])",
    r"\.claude/settings\.local\.json(?![\w-])", r"api-key", r"api-token", r"hosts\.yml(?![\w-])",
    # A credentials FILE: after a slash (~/.aws/credentials, ./credentials) or with an extension
    # (credentials.json). The bare word in prose or a search pattern is not a path (2026-10-01:
    # it blocked a memory note and a grep for the word).
    r"auth\.json(?![\w-])",
    r"(?:/credentials(?:\.\w+)?|(?<![\w-])credentials\.(?:json|ya?ml|toml|ini|txt|cfg|conf))(?![\w-])",
    r"\.pem(?![\w-])",
    r"\.p8(?![\w-])", r"id_(?:rsa|ed25519|ecdsa)(?!\.pub)(?!\w)", r"gateways\.json", r"mcp-token\.txt",
    r"\.aws/", r"\.netrc(?!\w)", r"\.npmrc(?!\w)", r"\.pgpass(?!\w)", r"\.docker/config\.json",
    r"gcloud/[^\s'\"]*\.json",
]
SENS = [(n, re.compile(r)) for n, r in zip(SENSITIVE, SENS_RES)]
ENV_RE = re.compile(r"(?<![\w-])\.env(?P<suf>\.[\w.-]+)?(?![\w-])")
ENV_OK = {"example", "sample", "template"}

READERS = ("cat tac head tail less more bat nl jq yq strings xxd od base64 sed awk cut sort uniq "
           "grep rg diff paste fold fmt column tr rev expand unexpand hexdump hd zcat bzcat xzcat zgrep "
           "xargs source view vi vim nano ed emacs openssl ssh-keygen gawk mawk dd comm join").split()
VERB = re.compile(r"(?<![\w.-])(" + "|".join(READERS) + r")(?![\w.-])")
# git commands that print a file's content or a diff of it, and an input redirect ("< file", "while read ... done < file")
# The two alternatives below cannot match the same word (the second refuses the option names the first takes), so the
# repeat has one way to match and cannot blow up on a long run of options.
_GIT = (r"(?<![\w.-])git\s+(?:(?:-C|-c|--git-dir|--work-tree|--namespace|--exec-path)\s+\S+\s+"
        r"|-(?!(?:C|c|-git-dir|-work-tree|-namespace|-exec-path)\s)\S+\s+)*")
GIT_PRINT = re.compile(_GIT + r"(?:show|cat-file|blame|diff|grep|stash\s+show)(?![\w-])"
                       r"|" + _GIT + r"log\b[^;&|]*\s(?:-p|--patch|-[a-zA-Z]*p[a-zA-Z]*)(?!\S)")
REDIR_IN = re.compile(r"(?<!<)<(?![<(&])\s*")
INTERP = re.compile(r"(?<![\w.-])(?:python3?|node|ruby|perl)(?![\w.-])")
INTERP_EVAL = re.compile(r"(?<!\S)(?:-[a-zA-Z]*[ce]|--eval)(?!\S)|<<")
SHELL_C = re.compile(r"(?<![\w.-])(?:ba|z)?sh\s+-\w*c\b")
GREP_QUIET = re.compile(r"(?<!\S)(?:-[a-zA-Z]*[clq]|-[a-zA-Z]*L[a-zA-Z]*|--count|--files-with-matches"
                        r"|--files-without-match|--quiet)(?!\S)")
HINT = ("Inspect it with: python3 ~/.claude/scripts/redact_view.py <file>. Never put secrets inline "
        "in commands; read them from a file or env var inside a script.")

# Rule E: a file that holds a REAL secret value (from env files or ~/.config/afa/secrets)
# is never read to the screen, whatever its name. The name-based rules miss a password pasted into
# a note, and redact_view only masks values under secret-looking keys, so a password in a note's
# plain text gets through both. The env-file list is cached for an hour because walking ~/repos
# takes about a second; the values themselves are read fresh on each check.
KS_REPOS = os.environ.get("KNOWN_SECRETS_REPOS") or os.path.expanduser("~/repos")
KS_SECRETS_DIR = os.environ.get("KNOWN_SECRETS_DIR") or os.path.expanduser("~/.config/afa/secrets")
KS_IGNORE = os.environ.get("KNOWN_SECRETS_IGNORE") or os.path.expanduser("~/.config/known-secrets/ignore.txt")
KS_CACHE = os.environ.get("KNOWN_SECRETS_CACHE") or os.path.expanduser("~/.claude/state/known-secrets-envfiles.json")
KS_TTL, KS_MAX = 3600, 5 * 1024 * 1024
_ks_values = None


def known_secrets():
    """[(bytes, 'envfile KEY')] for every form of every real value. Empty if the scanner is missing."""
    global _ks_values
    if _ks_values is not None:
        return _ks_values
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "known_secrets_scan.py")
    if not os.path.exists(src):
        _ks_values = []
        return _ks_values
    spec = importlib.util.spec_from_file_location("known_secrets_scan", src)
    ks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ks)
    files = None
    try:
        c = json.load(open(KS_CACHE))
        if c.get("root") == KS_REPOS and time.time() - c.get("generated", 0) < KS_TTL:
            files = c["files"]
    except (OSError, ValueError, KeyError):
        pass
    if files is None:
        files = list(ks.env_files(KS_REPOS))
        try:
            os.makedirs(os.path.dirname(KS_CACHE), exist_ok=True)
            fd = os.open(KS_CACHE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"root": KS_REPOS, "generated": time.time(), "files": files}, fh)
            os.replace(KS_CACHE + ".tmp", KS_CACHE)
        except OSError:
            pass
    found = ks.collect(KS_REPOS, ks.load_ignore(KS_IGNORE), files=files, secrets_dir=KS_SECRETS_DIR)
    _ks_values = [(n, "%s %s" % meta) for n, meta in ks.needles_for(found)]   # every written form
    return _ks_values


def secret_in_file(path):
    """'envfile KEY' of the first real secret value inside path, else None. Env files themselves
    are left to rules B/C (they hold secrets by definition and redact_view masks them)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > KS_MAX or ENV_RE.search(os.path.basename(path)):
        return None
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    for needle, where in known_secrets():
        if needle in data:
            return where
    return None


def reader_targets(cmd, cwd):
    """Existing files that a reader command (cat, sed, grep, ..., or redact_view.py) would print."""
    out = []
    for seg in split_segments(cmd):
        scan = blank_quotes(seg)
        verbs = set(VERB.findall(scan))
        if "redact_view.py" in seg:
            verbs.add("redact_view")
        if not verbs or (verbs <= {"grep", "rg"} and GREP_QUIET.search(scan)):
            continue
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        for t in toks:
            if t.startswith("-") or t in READERS or t.endswith("redact_view.py"):
                continue
            p = os.path.expanduser(t)
            if not os.path.isabs(p):
                p = os.path.join(cwd or os.getcwd(), p)
            if os.path.isfile(p):
                out.append(p)
    return out


RULE_E = ("secret-guard: rule E, %s holds a real secret value (%s). Don't print it. Count or test it "
          "inside a script that prints names only. If the value doesn't belong there, remove it: "
          "python3 ~/.claude/scripts/scrub_known_secrets.py --min-age 0 <file>")


def log_error(msg):
    try:
        path = os.environ.get("SECRET_GUARD_LOG") or os.path.expanduser(
            "~/.local/share/claude-hooks/secret-guard.log")
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg.replace("\n", " ")[:300]))
    except Exception:
        pass


def sensitive_in(text):
    """Return the label of the first credential-bearing path mentioned in text, else None."""
    for m in ENV_RE.finditer(text):
        if (m.group("suf") or "").lstrip(".") not in ENV_OK:
            return ".env" + (m.group("suf") or "")
    for name, rx in SENS:
        if rx.search(text):
            return name
    return None


def literal_value(val):
    v = val.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    if len(v) < 6 or v[0] in "$`/~" or v.startswith("./") or PLACEHOLDER.search(v):
        return False
    return not re.fullmatch(r"-?\d+(?:\.\d+)?|true|false", v, re.I)


def find_token(text):
    for label, rx in TOKENS:
        if re.search(rx, text):
            return label
    return None


def find_secret(cmd):
    label = find_token(cmd)
    if label:
        return label
    pos = 0
    while True:
        m = ASSIGN.search(cmd, pos)
        if not m:
            break
        pos = m.end()
        val = m.group("val")
        # Only the 40 characters before the name matter; an "a" stands in for a cut so the ^ in CLOSES_STRING still means the start of the command.
        opened = CLOSES_STRING.search(("a" if m.start() > 40 else "") + cmd[max(0, m.start() - 40):m.start()]) if val[0] in "\"'" else None
        if opened and opened.group(1) == val[0]:
            # Claude_Commandz issue 239: in line.startswith("NAME=") or grep "^NAME=" the quote after the = closes a string opened
            # before the name; it does not open a value. A value glued to that closing quote (export "NAME="abc) is the GLUED scan below.
            pos = m.start("val") + 1
            continue
        if m.start() and cmd[m.start() - 1] in "?&" and URL_ID.match(val) and "://" in re.split(r"\s", cmd[:m.start()])[-1]:
            continue
        if not NAME_NOT_SECRET.search(m.group("name")) and literal_value(val):
            return "literal value assigned to %s" % m.group("name").lstrip("-")[:40]
    for m in GLUED.finditer(cmd):
        # export "NAME="abc: bash joins the quoted name= to the word after it, so that word is the value (the quoted ASSIGN match above
        # needs a quoted value to close, and this one has none). A space, +, ., ), ], comma, ; & | } : < or > after the closing quote is code or text, not a value; any other character starts one.
        # Known limit: a password that itself starts with one of those characters, glued to a quoted NAME=, is not caught (main caught it only by accident).
        if not NAME_NOT_SECRET.search(m.group("name")) and literal_value(m.group("val")):
            return "literal value assigned to %s" % m.group("name").lstrip("-")[:40]
    for m in FLAG_SP.finditer(cmd):
        if literal_value(m.group("val")):
            return "literal value passed to a secret flag"
    return None


def split_segments(cmd):
    return split_segments_ops(cmd)[0]


def split_segments_ops(cmd):
    """The segments of split_segments, and for each one the separator character that stood in front of it ("" for the first)."""
    segs, ops, cur, q, i = [], [], [], None, 0
    last = ""
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and i + 1 < len(cmd) and cmd[i + 1] != "\n" and q != "'":
            cur.append(c + cmd[i + 1])  # Claude_Commandz issue 234: an escaped \& \; \| \" is part of its word, not a separator or a quote
            i += 2                      # (backslash-newline still ends the segment, so a path broken across lines is still cut apart)
            continue
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in ";&|\n":
            segs.append("".join(cur))
            ops.append(last)
            last = c
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    segs.append("".join(cur))
    ops.append(last)
    return segs, ops


# Workspace issue 93: the mail tool's token (~/.config/afa/gmail/token.json) holds gmail.modify, a scope Google lets
# send mail with, and no Gmail scope that can still make drafts is send-free. The tool itself never sends, so the
# token is kept from everything else. A text check can't stop a command that builds its path or address at run time, so
# this is a backstop: the folder is an ALLOWLIST (a command that names it passes only as the mail tool, the masked
# viewer or a plain existence check), and a send address is refused wherever it is written out.
GMAIL_DIR = re.compile(r"(?<![\w-])afa/gmail(?![\w-])|(?<![\w-])gmail/(?:token|client|calendar-token)[\w.-]*\.json")
GMAIL_SEND = re.compile(r"(?:messages|drafts)/(?:[\w-]+/)?send(?![\w-])|(?:messages|drafts)(?:/[\w-]+)?:send(?![\w-])", re.I)
# Claude_Commandz issue 258: a Python Google client spells the same action as svc.users().messages().send(...) (or .drafts().send), a Node one
# as gmail.users.messages.send(...), with no REST path in it. GMAIL_SEND_CALL is the call across spaces and rows (the resource fetched by getattr
# too, the method held as a reference or fetched by getattr). A messages() or drafts() resource and a .send somewhere in the same text
# (GMAIL_RESOURCE_CALL, GMAIL_SEND_NAME), or a Google client marker with the words messages (or drafts) and send (GMAIL_CLIENT, GMAIL_RESOURCE_WORD,
# GMAIL_SEND_WORD), cover the call split over a variable or built by name; all of those refuse as unclear. Letters are NFKC-folded first, as Python
# does for identifiers (a fullwidth s in .send runs as .send).
GMAIL_SEND_CALL = re.compile(r"(?<![\w-])(?:messages|drafts)\s*\)?\s*(?:\(\s*\))?\s*\.\s*send(?![\w-])|"
                             r"(?<![\w-])(?:messages|drafts)\s*\)?\s*\(\s*\)\s*,\s*send(?![\w-])", re.I)
GMAIL_RESOURCE_CALL = re.compile(r"(?<![\w-])(?:messages|drafts)\s*\)?\s*\(\s*\)", re.I)
GMAIL_SEND_NAME = re.compile(r"[.,]\s*send(?![\w-])", re.I)
GMAIL_CLIENT = re.compile(r"googleapiclient|googleapis|users\s*\(|build\s*\(\s*gmail", re.I)
GMAIL_RESOURCE_WORD = re.compile(r"(?<![\w-])(?:messages|drafts)(?![\w-])", re.I)
GMAIL_SEND_WORD = re.compile(r"(?<![\w-])send(?![\w-])", re.I)
GMAIL_PLAIN = {"ls", "stat", "test", "[", "[[", "realpath", "readlink"}  # none of these reads a file's content (wc, du and file can)
GMAIL_SCRIPTS = ("~/.claude/scripts/redact_view.py", "~/.claude/scripts/gmail_api.py")  # the only scripts that may run against the folder
GMAIL_QUOTE_WORDS = {"grep", "rg", "git"}  # a quoted mention (a search pattern, a commit message) is not a call
GMAIL_GIT_QUOTING = {"commit", "tag", "status"}  # git subcommands that take a message and run no pager, diff driver or alias
GMAIL_ENV_INJECT = re.compile(r"(?<![\w-])(?:PYTHON\w*|LD_\w+|BASH_ENV|GIT_\w+)=")  # variables that make an allowed script load other code
GMAIL_GLOB = re.compile(r"[*?\[{]")
GMAIL_AFA_GLOB = re.compile(r"\.con[^\s/]*[*?\[{][^\s/]*/|\.config/[^\s/]*[*?\[{][^\s/]*/|\.config/afa/[^\s]*[*?\[{]|\.config/af[^\s/]*[*?\[{]")  # a glob that can land on ~/.config/afa/gmail
ALIAS_DEFINER = re.compile(r"(?<![\w.-])(?:alias|BASH_ALIASES|eval|source|expand_aliases|BASH_ENV)(?![\w-])|(?:^|[\s;&|(])\.\s+(?:--(?![\w-])|-p(?!\w)|-(?![\w-])|[^\s-])|\.(?:bashrc|bash_profile|bash_login|profile|zshrc|zshenv)(?![\w-])")  # a way to put text into the shell's alias table or its input
# ALIAS_DEFINER is also searched with quotes and backslashes removed (al""ias), and with _echo_shadowed (set -o posix, shopt, builtin, command);
# a command word that starts with a variable ($a$b x=...) can build the word alias at run time. The dot-source alternative also matches
# find . -name x: that only widens the quote-blind pass, which refuses any piece that names or globs into the token folder.
# A dot followed by an option word (. -x, . --no-banner, find . -name x, docker run image dir . --redact) is not a source: bash and dash both
# refuse ". -x" as an invalid option (tested), so the alternative needs a file word that does not start like an option. These still count: ". -- x", ". --$y"
# (the expansion may split into -- and a file), ". -p dir file" (bash 5.3 source -p), ". -" and every word that starts with a letter, a digit or a path.
VARIABLE_COMMAND_WORD = re.compile(r"(?:^|[;&|\n){!]|(?<![\w.-])\(|(?<![\w.-])(?:then|do|else|elif|if|while|until|in)\(?|(?<![\w.-])time(?:[ \t]+-p)?(?:[ \t]+--)?[ \t]*\(?|(?<![\w.-])coproc(?:[ \t]+\w+)?[ \t]*\(?)[ \t]*"
                                   r"(?:(?:[A-Za-z_]\w*=\S*|\d*[<>]+&?[ \t]*\S+|\{\w+\}[<>]+&?[ \t]*\S+|&>>?[ \t]*\S+)[ \t]+)*[\w.-]*[$`{*?\[]")  # assignments and redirects (a space before the target too) may stand in front
# the same, widened after the second security scan: any command word that holds a $, a backtick or a brace (al$b, `echo alias`, {ali,as}) can build the word alias
# Security scan of the 241 fix: a function (ls() { cat "$@"; }), hash, export and a changed PATH or ENV also make a command word run something else.
QUOTE_SHIFTER = re.compile(r"\$\(|`|\$['\"]")  # a command substitution, a backtick, or an ANSI-C ($'..') or locale ($"..") quoted word
NAME_SHADOW = re.compile(r"[\w.-]+[ \t]*\([ \t]*\)|(?<![\w.-])(?:hash|export|enable|unalias)(?![\w-])|(?<![\w-])(?:PATH|ENV|BASH_FUNC\w*)=")
GMAIL_CD_CONFIG = re.compile(r"(?:^|[\s;&|])(?:cd|pushd)\s+[^\s;&|]*\.config(?:/afa)?/?(?=\s|;|&|\||$)")  # a cd into the folder's parents


_ANSI_ESC = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def _ansi_c_decode(m):
    """The text a $'..' word holds once bash has read its escapes (\\xHH, \\NNN, \\uHHHH, \\n, \\' and so on); an unknown escape keeps its letter."""
    s, out, i = m.group(1), [], 0
    while i < len(s):
        c = s[i]
        if c != "\\" or i + 1 >= len(s):
            out.append(c)
            i += 1
            continue
        d = s[i + 1]
        num = re.match(r"x[0-9a-fA-F]{1,2}|[0-7]{1,3}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}", s[i + 1:])
        if num:
            t = num.group(0)
            try:
                out.append(chr(int(t[1:], 16) if t[0] in "xuU" else int(t, 8) & 0xFF))
            except (ValueError, OverflowError):
                out.append("?")
            i += 1 + len(t)
        else:
            out.append(_ANSI_ESC.get(d, d))
            i += 2
    return "".join(out)


def names_send(norm):
    """True when text (already through gmail_norm) writes a Gmail send address, or calls a Google client's send (issue 258)."""
    norm = unicodedata.normalize("NFKC", norm)
    return bool(GMAIL_SEND.search(norm) or GMAIL_SEND_CALL.search(norm) or (GMAIL_RESOURCE_CALL.search(norm) and GMAIL_SEND_NAME.search(norm))
                or (GMAIL_CLIENT.search(norm) and GMAIL_RESOURCE_WORD.search(norm) and GMAIL_SEND_WORD.search(norm)))


def gmail_norm(text):
    """The text as the shell would spell the path: $'..' escapes decoded, a $ before a quote gone, quotes and backslashes gone, ~ and $HOME written out, ./ and x/../ folded."""
    home = os.path.expanduser("~")
    t = re.sub(r"\$'((?:[^'\\]|\\.)*)'", _ansi_c_decode, text)  # issue 236: $'fa' is fa, $'\x61' is a
    t = re.sub(r"\$(?=[\"'])", "", t)  # $"fa" and a $' left over are the plain quote
    t = re.sub(r"[\"'\\]", "", t)
    t = re.sub(r"\$\{?HOME\}?|(?<![\w])~(?=/|\s|$)", home, t)
    t = re.sub(r"/{2,}", "/", t)
    t = re.sub(r"/\./", "/", t)
    while True:
        folded = re.sub(r"/[^/\s]+/\.\./", "/", t, count=1)
        if folded == t:
            return t
        t = folded


def gmail_words(seg):
    """The command's words with leading VAR=value assignments dropped; None when the segment can't be split."""
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        return None
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        words.pop(0)
    return words


def gmail_segment_ok(seg):
    """A segment that names the token folder is fine only as the mail tool, the masked viewer or a plain check."""
    if "$(" in seg or "`" in seg or "<" in seg:  # a substitution, or any input redirect, here-document or here-string
        return False
    if GMAIL_GLOB.search(seg) and GMAIL_AFA_GLOB.search(gmail_norm(seg)):  # a glob can reach the folder without naming it
        return False
    words = gmail_words(seg)
    if not words:
        return False
    first = os.path.basename(words[0])
    if first in GMAIL_PLAIN:
        return True
    if first in {"grep", "rg"}:
        return bool(GREP_QUIET.search(" ".join(words[1:])))
    if re.fullmatch(r"python[\d.]*", first):
        try:
            lead = shlex.split(seg, posix=True)
        except ValueError:
            return False
        for w in lead:  # only AFA_GMAIL_DIR may be set in front: PYTHONPATH, PYTHONSTARTUP and the like load other code
            if not re.match(r"^[A-Za-z_]\w*=", w):
                break
            if not w.startswith("AFA_GMAIL_DIR="):
                return False
        script = None
        for w in words[1:]:
            if w in ("-I", "-u", "-B", "-E", "-s", "-S", "-O", "-OO"):
                continue
            if w.startswith("-"):  # -c, -m, - (the script on stdin) and everything else
                return False
            script = w
            break
        if script is None:
            return False
        real = os.path.realpath(os.path.expanduser(gmail_norm(script)))
        return real in {os.path.realpath(os.path.expanduser(p)) for p in GMAIL_SCRIPTS}
    return False


# Claude_Commandz issue 238: Python's glob does not expand what bash expands before it globs: brace lists ({c,x}, {1..3}) and POSIX classes
# ([[:alpha:]]). bash_glob does both, bounded, and yields None (the caller refuses) for any form it does not understand.
POSIX_CLASSES = {"alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "lower": "a-z", "upper": "A-Z", "xdigit": "0-9a-fA-F",
                 "word": "a-zA-Z0-9_", "punct": ":-@{-~_^!-/", "space": " \t\n", "blank": " \t", "print": " -~", "graph": "\"-~!", "cntrl": "\x00-\x1f"}
BRACE_LIMIT = 256


def _brace_expand(s, budget):
    """The bash brace expansion of s as a list, or None when it holds a form not understood or yields more than budget words."""
    depth, start = 0, None
    for i, c in enumerate(s):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth:
            depth -= 1
            if depth == 0:
                inner = s[start + 1:i]
                parts, d, cur = [], 0, []
                for ch in inner:
                    if ch == "{":
                        d += 1
                    elif ch == "}":
                        d -= 1
                    if ch == "," and d == 0:
                        parts.append("".join(cur))
                        cur = []
                    else:
                        cur.append(ch)
                parts.append("".join(cur))
                if len(parts) == 1:
                    seq = re.fullmatch(r"(-?\d+)\.\.(-?\d+)|([A-Za-z])\.\.([A-Za-z])", inner)
                    if seq:
                        if seq.group(1) is not None:
                            a, b = int(seq.group(1)), int(seq.group(2))
                            if abs(b - a) >= budget:
                                return None
                            parts = [str(n) for n in (range(a, b + 1) if a <= b else range(a, b - 1, -1))]
                        else:
                            a, b = ord(seq.group(3)), ord(seq.group(4))
                            parts = [chr(n) for n in (range(a, b + 1) if a <= b else range(a, b - 1, -1))]
                    elif ".." in inner:
                        return None  # a sequence with a step or padding: not understood
                    else:
                        rest = _brace_expand(s[i + 1:], budget)  # a lone {word} is literal text in bash
                        return None if rest is None else [s[:i + 1] + r for r in rest]
                tails = _brace_expand(s[i + 1:], budget)
                if tails is None:
                    return None
                heads = []
                for p in parts:
                    sub = _brace_expand(p, budget)
                    if sub is None:
                        return None
                    heads += sub
                if len(heads) * len(tails) > budget:
                    return None
                return [s[:start] + h + t for h in heads for t in tails]
    return [s]


def bash_glob(pattern):
    """Yield every file the shell's glob would give for pattern after brace expansion and POSIX classes, and None once for a form not understood."""
    words = _brace_expand(pattern, BRACE_LIMIT)
    if words is None:
        yield None
        return
    for w in words:
        w = os.path.expanduser(gmail_norm(w))  # bash does tilde and $HOME after the braces: {~,x}/... and $HO{M,x}E/... are the home folder
        w0 = w
        w = None if len(w) > 2000 or w.count("[") > 64 else _bracket_translate(w)  # a word this long is not a path to read: refuse, and keep the hook inside its time limit
        if w is None:
            yield None
            return
        # Issue 260: bash 5.2.15 and 5.2.37 read an unclosed [. differently (h[[.a] is the letters h[ plus a bracket, or one bracket holding
        # [ . a), so both readings are followed and a file either one reaches counts.
        other = _bracket_translate(w0, outer_literal=False) if "[." in w0 else None
        for m in glob.iglob(w):
            yield m
        if other is not None and other != w:
            for m in glob.iglob(other):
                yield m


def _bracket_translate(w, outer_literal=True):
    """w with every bracket expression written the way Python's glob reads it (a ^ first is !, a POSIX class is its characters), or None
    for a form not translated exactly: an equivalence class [=x=], a collating symbol [.x.] or an unknown class name. A [:name:] outside
    a bracket is just an ordinary bracket and stays as it is."""
    out, i, n = [], 0, len(w)
    while i < n:
        c = w[i]
        if c != "[":
            out.append(c)
            i += 1
            continue
        j = i + 1
        neg = j < n and w[j] in "!^"
        if neg:
            j += 1
        body, wide = [], False
        if j < n and w[j] == "]":  # a ] first is a letter
            body.append("]")
            j += 1
        literal_outer = False
        while j < n and w[j] != "]":
            if w[j] == "[" and j + 1 < n and w[j + 1] in "=.":
                # Issue 260: [. and [= start a collating symbol or an equivalence class only when the matching .] or =] closes it; a bare [.name]
                # (a jq filter in a gh -q word) is an ordinary bracket. The search starts at the character after the [, so [.] is also refused.
                if w.find(w[j + 1] + "]", j + 1) >= 0:
                    return None
                if w[j + 1] == "." and outer_literal:
                    # bash 5.2.15 (checked): an unclosed [. makes the OUTER [ a literal letter and carries on just after it, so h[[.a] matches only the
                    # file named h[a. Python's glob would read one bracket, so the outer [ is written as [[] and the rest is read again from there.
                    literal_outer = True
                    break
                body.append(w[j])  # an unclosed [= is an ordinary letter of the bracket (and, with outer_literal False, an unclosed [. too: bash 5.2.37)
                j += 1
                continue
            if w[j] == "[" and j + 1 < n and w[j + 1] == ":":
                end = w.find(":]", j + 2)
                if end < 0:
                    body.append(w[j])
                    j += 1
                    continue
                name = w[j + 2:end]
                if name not in POSIX_CLASSES or (body and body[-1] == "-") or w[end + 2:end + 3] == "-":
                    return None  # an unknown name, or a class standing at the end of a range: not translated exactly
                body.append(POSIX_CLASSES[name])
                if name in ("punct", "graph", "print", "cntrl"):
                    wide = True  # these hold characters a Python bracket cannot spell ( ] \ ` and control bytes ): the whole bracket becomes ?, which can only match more
                j = end + 2
                continue
            body.append(w[j])
            j += 1
        if literal_outer:
            out.append("[[]")
            i += 1
            continue
        if j >= n:  # no closing ]: the [ is an ordinary letter
            out.append("[")
            i += 1
            continue
        out.append("?" if wide else "[" + ("!" if neg else "") + "".join(body) + "]")
        i = j + 1
    return "".join(out)


SHELLISH = {"bash", "sh", "zsh", "dash", "ksh", "eval", "source", ".", "xargs", "env", "python", "python3", "perl", "node", "ruby", "exec", "command", "builtin",
            "timeout", "nice", "nohup", "stdbuf", "setsid", "busybox", "fish", "csh", "tcsh", "awk", "gawk", "php", "lua", "pwsh", "ssh", "sudo", "doas"}
# Blind re-review of PR 266: the operator sits right after the parameter name (and a subscript); a lazy "anything up to ?" took the ? of a glob inside
# ${x/a/~/.?onfig/..} for the ${x?word} operator and rebuilt the wrong word
# (security review: ${!x:-w} is the indirect form, and a pattern may hold an escaped slash or a quote: ${x/a\/b/word}, ${x/"a/b"/word})
_EXPANSION_HEAD = r"\$\{!?(?:\w+|[@*#?$!-])(?:\[[^\]]*\])?"
_REPLACE_PAT = r"/[/#%]?(?:\\.|\"[^\"]*\"|'[^']*'|[^{}/\\\"'])*/"
EXPANSION_START = re.compile(_EXPANSION_HEAD + r"(?::?[-+=?]|" + _REPLACE_PAT + ")")  # ${x:-  ${x=  ${x?  ${x/pat/  ${x//pat/ : what follows is a word of its own
DEFAULT_EXPANSION = re.compile(_EXPANSION_HEAD + r":?[-+=?]([^{}]*)\}|" + _EXPANSION_HEAD + _REPLACE_PAT + r"([^{}]*)\}")  # ${x:-word} and ${x/pat/word}: the word is what runs when x is empty or matches


def _command_index(words):
    """Index of the command word: past NAME=value words and the command and builtin prefixes (with their options)."""
    i = 0
    while i < len(words):
        w = words[i]
        if (re.match(r"^[A-Za-z_]\w*=", w) or w in ("command", "builtin", "exec", "{", "(", "!", "if", "then", "do", "else", "elif", "while", "until", "time")
                or (i > 0 and words[i - 1] in ("command", "builtin") and w.startswith("-"))):
            i += 1
            continue
        break
    return i


def _is_cd_segment(seg):
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        return True  # unclear: counts as a cd, so later relative words are checked more widely
    i = _command_index(words)
    # a cd inside a group or compound command ((cd ~ && ..), { cd ~; .. }, if cd ~; then ..) counts too, and so does any cd word anywhere in the text
    return (i < len(words) and os.path.basename(words[i]).lstrip("({!") in ("cd", "pushd")) or bool(re.search(r"(?<![\w.-])(?:cd|pushd)(?![\w.-])", seg))


def _feeds_shell(text):
    """True when some segment of the text runs a shell, an interpreter or xargs: an echoed or here-string text is then code, not prose."""
    for seg in split_segments(text):
        try:
            words = shlex.split(seg, posix=True)
        except ValueError:
            return True
        i = _command_index(words)
        if i < len(words) and os.path.basename(words[i]) in SHELLISH:
            return True
    return False


def _defaults_applied(c, empty=False):
    """The word with every ${x:-word} (and ${x/pat/word}) replaced by its word at once, innermost first; with empty, every other $name or ${..} removed."""
    for _ in range(40):
        m = DEFAULT_EXPANSION.search(c)
        if not m:
            break
        c = c[:m.start()] + (m.group(1) if m.group(1) is not None else m.group(2)) + c[m.end():]
    if empty:
        c = re.sub(r"\$\{[^{}]*\}|\$\w+", "", c)
    return c


def _pattern_reaches(folder, base, pattern, below_only=False):
    """True when a relative glob pattern, standing in base, can match the token folder, a folder above it or a path below it. The pattern's parts are
    matched against the folder's real parts with fnmatch, a part that starts with a dot matching only a part that starts with a literal dot (or [ or {),
    so nothing is walked on disk and */*/* from / is not a hit. Anything not understood is a hit."""
    if not (folder + os.sep).startswith(base.rstrip(os.sep) + os.sep):
        return False
    fparts = [c for c in folder[len(base.rstrip(os.sep)):].split(os.sep) if c]
    expanded = _brace_expand(pattern, BRACE_LIMIT)
    if expanded is None:
        return True
    for one in expanded:
        parts = [c for c in os.path.normpath(one).split(os.sep) if c not in ("", ".")]
        # what is left after normpath is a leading ../ (a brace form such as ..{,}/.?onfig or {..,x}/.?onfig expands to it here, where the caller's _rel_variants never saw it):
        # the parts without it are tried from this base, as _rel_variants does for a plain word, so ../*.md is ordinary and ..{,}/.?onfig/a?a/g?ail is not (security review)
        while parts and parts[0] == "..":
            parts.pop(0)
        if not parts or any(c == ".." for c in parts):
            continue
        if len(parts) < len(fparts) - (0 if below_only else 2):
            continue  # a pattern that stops above ~/.config lands on an ordinary folder (*/ from /), and one above the folder lands on a parent, which below_only does not count
        # security review of PR 266: each part is written the way Python's fnmatch reads a bracket ([^x] is [!x], a POSIX class is its characters) first;
        # a part that cannot be translated exactly is a hit. An unclosed [. is read both ways, as bash_glob does.
        readings = []
        for c in parts:
            first = _bracket_translate(c)
            if first is None:
                return True
            readings.append([first] + ([_bracket_translate(c, outer_literal=False)] if "[." in c else []))
        if any(r is None for rs in readings for r in rs):
            return True
        if parts and all(i >= len(fparts) or ((not fparts[i].startswith(".") or c.startswith((".", "[", "{"))) and any(fnmatch.fnmatchcase(fparts[i], r) for r in readings[i]))
                         for i, c in enumerate(parts)):
            return True
    return False


def _glob_part_names_token_folder(c):
    """True when a path part with a glob in it and a literal letter or digit (a?a, g?ail, .?onfig) can match afa, gmail or .config; one that cannot be
    translated exactly counts as a hit. A bare * or ?* has no literal and is ordinary."""
    if not GMAIL_GLOB.search(c) or not re.search(r"[A-Za-z0-9]", c):
        return False  # (letters inside a bracket count: [a][f][a] is afa)
    forms = _brace_expand(c, BRACE_LIMIT)
    if forms is None:
        return True
    for one in forms:
        t = _bracket_translate(one)
        if t is None:
            return True
        if any(fnmatch.fnmatchcase(name, t) for name in ("afa", "gmail") + ((".config",) if one.startswith((".", "[", "?")) else ())):
            return True
    return False


def _rel_variants(w):
    """A relative word and the same without its leading ../ parts: after a cd below home, ../.config is the home folder's .config."""
    norm = os.path.normpath(w) if w else w
    parts = [c for c in norm.split(os.sep) if c not in ("", ".", "..")]
    return {w, norm, os.sep.join(parts)} - {""}


def gmail_resolves_into_folder(seg, tolerant=False, rel=False, prose_ok=True, level=0, strict=False):
    """True when a word of the segment, once globs are expanded and symlinks followed, is the token folder or lands inside it,
    or the segment is a cd/pushd into the folder or one of its parents (~/.config/afa, ~/.config). This looks at the real
    file system, so a spelling the text patterns miss (a symlink, a glob, a ./ or ../ path) is still seen.
    tolerant is for a line of an interpreter's heredoc body, which is that interpreter's code and not shell: a line the shell
    could not split (a lone apostrophe in a comment) is cut at quotes, brackets, commas and spaces, and every piece is still checked."""
    folder = os.path.realpath(os.path.expanduser("~/.config/afa/gmail"))
    parents = {os.path.dirname(folder), os.path.dirname(os.path.dirname(folder))}
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        if not tolerant:
            return True  # a segment that can't be split can't be checked
        words = []
        # a row with a lone apostrophe, usually in a trailing comment: split what comes before each # or quote that could start the unsplittable
        # part, so a quoted glob in front (cat ~/.?on"f"ig/a?a/g?ail/token.json # it's) is still read as the shell reads it
        marks = list(re.finditer(r"[#'\"]", seg))
        if len(marks) > 64:
            return True  # too many to check in the hook's time: refuse rather than guess
        for m in marks:
            try:
                words += shlex.split(seg[:m.start()], posix=True)
            except ValueError:
                continue
    if tolerant:  # the pieces too, so open('/a/link/x') and a line the shell can't split are both followed to the real path
        # also the row with its quotes and backslashes removed, as bash would join a quoted glob (~/.?on"f"ig) into one word
        words = words + [w for w in re.split(r"[\s'\"(),;<>]+", seg) + re.split(r"[\s(),;<>]+", re.sub(r"[\"'\\]", "", seg)) if w]
    # Issue 257: a word that holds spaces is the text of a bash -c or sh -c argument or of a here-string (bash <<< 'cat ~/.?onfig/..'): it is a command
    # line of its own, so every piece of it is checked as a word as well. Too many pieces to check in the hook's time are refused.
    # Text that only prints or stores (echo, printf, cat and the like, gh issue, git commit) is prose, as in issues 228 and 235, and stays one word.
    lead = os.path.basename(words[0]) if words else ""
    prose = (lead in ECHO_WORDS or lead in HEREDOC_DATA_WORDS or (lead == "gh" and len(words) > 1 and words[1] in HEREDOC_DATA_GH)
             or (lead == "git" and len(words) > 1 and words[1] in GMAIL_GIT_QUOTING)) and not re.search(r"\$|[<>]\(|`", seg) and prose_ok  # any $ expansion (${ cmd; } too), or a shell later in the line that the text could be piped to, ends the prose exemption
    if not prose:
        spaced = [w for w in words if re.search(r"\s", w)]
        words = words + [piece for w in spaced for piece in re.split(r"[\s;&|]+", w) if piece]
        # Security review of PR 266: the text of a bash -c word or a here-string is a command line of its own, so its own cd words set the folder for
        # what follows it; it is read again as segments (two levels deep at most, and not past a size that fits the hook's time)
        if spaced and level < 2:
            if len(spaced) > 20 or sum(len(w) for w in spaced) > 8000:
                return True
            for w in spaced:
                inner_rel = rel
                for inner in split_segments(w):
                    # tolerant: a piece of ordinary text (a lone apostrophe in a message, an unbalanced quote in a regex) can't be split as shell and is
                    # not refused for that; it is cut at quotes and spaces and every piece is still checked as a path
                    if inner.strip() and gmail_resolves_into_folder(inner, True, inner_rel, prose_ok, level + 1):
                        return True
                    inner_rel = inner_rel or _is_cd_segment(inner)
        elif spaced and any(re.search(r"(?<![\w.-])(?:cd|pushd)(?![\w.-])", w) for w in spaced):
            return True  # blind re-review of PR 266: a cd three shells deep sets a folder the hook no longer follows, so it is refused rather than guessed
    elif level < 2:
        # Blind review of PR 266: prose that is echoed, stored or posted may still be code later (piped to a wrapped shell, written to a file and run),
        # whichever shell or wrapper that is, so the list of shells decides nothing here: a path-like glob piece of a spaced word that lands IN the
        # token folder (or below it) is refused; a mention of a parent folder (~/.config) is still prose
        paths = [piece for w in words if re.search(r"\s", w) for piece in re.split(r"[\s;&|]+", w)
                 if piece and re.search(r"[*?\[{]", piece) and ("/" in piece or piece.startswith(("~", ".")))]
        if len(paths) > 200:
            return True
        for piece in paths:
            if gmail_resolves_into_folder(shlex.quote(piece), True, rel, False, level + 1, True):
                return True
    if len(words) > 2000:
        return True
    ci = _command_index(words)  # past NAME=value words and command or builtin in front of cd
    cd = ci < len(words) and os.path.basename(words[ci]).lstrip("({!") in ("cd", "pushd")
    home = os.path.realpath(os.path.expanduser("~"))
    bases = {home, os.sep}  # a relative cd target starts wherever an earlier cd left the shell: home, every ancestor of it and / (not the token folder's own parents: cd .. from there is ordinary)
    up = home
    while os.path.dirname(up) != up:
        up = os.path.dirname(up)
        bases.add(up)

    def glob_inside(cand, cap_hit=True, below=False):  # every file a glob word can match, expanded as bash would, is checked; a form not understood is refused,
        n = 0                                          # and so are too many matches unless cap_hit is False (then the first 500 are checked, the rest left alone)
        for m in bash_glob(os.path.expanduser(gmail_norm(cand))):
            n += 1
            if m is None or inside(m, below):
                return True
            if n > 500:
                return cap_hit
        return False

    def inside(path, below=False):  # the folder, below it, or one of its parents (a find, grep -r, cp -r or tar of a parent walks into it); below: not the parents
        real = os.path.realpath(os.path.expanduser(gmail_norm(path)))
        return real == folder or (real in parents and not (strict or below)) or real.startswith(folder + os.sep)

    def from_bases(word, below=False):  # a relative word (and the same without its leading ../ parts) tried from every folder the shell may stand in
        # below: a glob that lands on a parent (*/* from /) is not a hit; a relative word after a cd is tried that way, since the shell stands in an unknown base
        # security review of PR 266: a plain path is judged as ever (a parent such as .config/afa after a cd home is a hit); below is for globs only
        below = below or strict
        for variant in _rel_variants(word):
            if variant.startswith(("/", "~")):
                continue
            for base in bases:
                if inside(os.path.join(base, variant), strict):
                    return True
                if GMAIL_GLOB.search(variant):
                    # the parts are matched against the folder's real parts (nothing walked), and, outside prose, the files the glob really matches under
                    # base are checked too, so a symlink that leads into the folder is followed (a broad glob is checked as far as its first 500 matches)
                    if _pattern_reaches(folder, base, variant, below) or (not strict and glob_inside(os.path.join(base, variant), False, below)):
                        return True
        return False

    for i, w in enumerate(words):
        if not w:
            continue
        if cd and i > ci and not w.startswith("-"):
            if inside(w):
                return True
            if GMAIL_GLOB.search(w) and glob_inside(w):  # issue 257: cd ~/.?onfig/a?a/g?ail goes into the folder
                return True
            if not w.startswith(("/", "~", "$")) and from_bases(w):  # a relative target starts wherever an earlier cd left the shell: try it from the folders that matter
                return True
            if "$" not in w and "~" not in w and "`" not in w:
                continue
            # fifth review: a target written as an expansion (cd ${x:-~/.?onfig/a?a/g?ail}, cd $PWD/.?onfig/.., cd ~+/..) goes through the general candidates below as well
        # an option's value (--file=PATH, -fPATH is not seen) and an @FILE word are paths too
        after_eq = w.split("=", 1)[1] if "=" in w else ""
        # security review of PR 266: a variable earlier in the path can hold home or any parent, so a later part that is a glob able to match afa, gmail or .config
        # ($x/$y/g?ail/token.json with y=a?a set in another word) is refused wherever the variable's value was set
        for c in (w, after_eq):
            cs = c.split("/")
            var_at = next((j for j, p in enumerate(cs) if "$" in p), None)
            if var_at is not None and any(_glob_part_names_token_folder(p) for p in cs[var_at + 1:][:60]):
                return True
        # blind re-review of PR 266: x=.?onfig holds a glob that bash expands where $x is used (cat $x/a?a/g?ail/token.json), so the value of an assignment is judged
        # as a path on its own, parents included, from home and every folder above it, whether or not a cd came first
        if re.match(r"[A-Za-z_]\w*=", w) and after_eq and not after_eq.startswith(("/", "~", "$")) and GMAIL_GLOB.search(after_eq) and from_bases(after_eq):
            return True
        # security review: a value split over several assignments (y=a?a; cat $x/$y/g?ail/...) is never rebuilt here, so an assignment whose value holds a glob part able
        # to match afa, gmail or .config is refused on its own
        if re.match(r"[A-Za-z_]\w*=", w) and any(_glob_part_names_token_folder(p) for p in after_eq.split("/")[:60]):
            return True
        # a redirect glued to its path (<./lnk/token.json, 0<path, 2>path, x>path) is that path: check every piece cut at the redirect operators
        # (and a path glued to a closing parenthesis or a backtick, as in $(cat ~/.?onfig/a?a/g?ail/token.json), is that path)
        # issue 257: ${x:-PATH} ${x:+PATH} ${x:=PATH} ${x:?PATH} (and the forms without the colon) hold PATH as a word of their own, closing brace cut off
        cands = {p.lstrip("@") for c in (w, after_eq) for p in [c] + re.split(r"[<>()`]+&?", c)}
        for c in (w, after_eq):
            exps = list(EXPANSION_START.finditer(c))
            if len(exps) > 40 or (exps and len(c) > 4000):
                return True  # a long word full of expansions is refused rather than rebuilt in the hook's time
            for m in exps:
                nest, close = 1, None
                for k in range(m.end(), len(c)):
                    nest += (c[k] == "{") - (c[k] == "}")
                    if nest == 0:
                        close = k
                        break
                inner = c[m.end():close] if close is not None else c[m.end():]
                rest = c[close + 1:] if close is not None else ""
                cands |= {inner.rstrip("}"), inner + rest, c[:m.start()] + inner + rest}  # the word with the expansion replaced by its default too
                # the shell's own split of ${x/pat/word} may differ from this one's (a backslash or quote in pat was already taken out by the word split): the text after
                # each slash of the body is tried as the word as well (security review)
                for k in [j for j, ch in enumerate(inner) if ch == "/"][:40]:
                    cands |= {inner[k + 1:].rstrip("}"), inner[k + 1:] + rest}
            if "$" in c:  # every default applied at once (left to right), and with every other variable empty: ${x:-~}/${y:-.?onfig}/a?a/..., ${IFS}/path
                cands |= {_defaults_applied(c), _defaults_applied(c, True)}
        unknown_rest = set()
        for c in (w, after_eq):
            if "$" in c or "~" in c:
                # blind review of PR 266: ~+ ~- $PWD ${PWD} $OLDPWD are the folder the shell stands in; ${HOME:0} ${HOME%x} are the home folder;
                # a path glued behind an expansion (cat${IFS}$HOME/.., $x$HOME/..) starts right after it; the rest of $var/rest is tried from every base
                same = re.sub(r"^(?:~[+-]|\$\{?(?:PWD|OLDPWD)\}?)(?=/|$)", ".", c)
                if same != c:
                    cands.add(same)
                cands.add(re.sub(r"\$\{HOME[^{}]*\}", "~", c))
                expansions = list(re.finditer(r"\$\{[^{}]*\}|\$\w+", c))
                if len(expansions) > 40:
                    return True
                for m in expansions:
                    cands.add(c[m.end():])
                m = re.match(r"\$\{?\w+\}?/(.+)", c)
                if m:
                    unknown_rest.add(m.group(1))
        for cand in cands:
            if cand and ("/" in cand or cand.startswith(("~", "."))):
                if inside(cand):
                    return True
                if GMAIL_GLOB.search(cand) and glob_inside(cand):
                    return True
                # after a cd (any, a bare cd goes home) a relative path starts wherever the shell then stands: tried from home, every ancestor of it and /
                if rel and not cand.startswith(("/", "~", "$")) and from_bases(cand, True):
                    return True
        if any(from_bases(r, True) for r in unknown_rest):
            return True
    return False


# Claude_Commandz issue 228: the body of a quoted heredoc (<<'EOF', <<"EOF", <<\EOF) is not read by the shell, so it is data, not commands, when the
# command line it feeds only stores or posts text. Word-splitting it as shell failed on any lone apostrophe (and the guard refuses what it can't
# split), and a send path in a sentence was read as a send. Such a body is taken out of the text that is word-split and checked for a send address;
# it is still searched for the token folder's name. The command it feeds stays checked in full whenever ANY command on its line is not on this short
# list (an interpreter, a shell, curl, xargs, eval, a path-qualified name, a variable assignment in front), and whenever anything is unclear.
HEREDOC_DATA_WORDS = {"cat", "tee", "wc", "uniq", "head", "tail", "tr", "cut"}  # not sort: --compress-program=sh pipes its input through a shell
HEREDOC_DATA_GH = {"issue", "pr", "release", "gist", "discussion"}
# A closed command substitution with a plain inside: one line, no nesting, no quote, backslash, comment mark, redirect or heredoc, no ; & | (so $(dirname "$f")
# is not one, $(dirname $f) is). It runs in a subshell, so nothing in it can change a name or an option of the command that holds it, and it holds nothing
# that bash would read as a quote, a heredoc or a comment: split_data_heredocs steps over it and _names_cannot_change reads the text without it
# Bash ends a substitution at the ) its parser reaches, which is the first ) only when nothing inside holds a ) of its own: a case pattern (x) cmd) or a
# ${..} word (${y:-)}). $[ ..] and name[..] pairs also swallow a ). So a { } [ ] or the word case keeps the text out of this pattern, and the whole call is read strictly as before.
# (issue 269, the hub's note case: mkdir -p $(dirname $N) on the line before a quoted heredoc made the body "opaque", and its apostrophe refused the call).
SIMPLE_SUBST = re.compile(r"\$\((?![^()`\"'\\\n<>;&|#{}\[\]]*(?<![\w.-])case(?![\w.-]))[^()`\"'\\\n<>;&|#{}\[\]]*\)")
HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\([\w.-]+)|([\w.-]+))")


def _heredoc_line_is_data(line, extra=()):
    """True when every command on this logical line only stores or posts its input (cat, tee, gh issue ..., git commit ...);
    extra names more bare commands that count (echo and printf, for a line that is not fed by a heredoc)."""
    if re.search(r">\|?[ \t]*[^\s;&|<>]*[*?\[{$]", line):
        return False  # a redirect target built from a glob or a variable can land on /dev/fd or /proc
    flat = re.sub(r"[\"'\\]", "", line)  # quotes and backslashes inside a redirect target do not hide its real path
    if "$(" in line or "`" in line or ">(" in flat or "<(" in flat or re.search(r">&\s*(?![12](?![\w$]))|&>|\{\w+\}\s*[<>]|/dev/|/proc/", flat):
        return False  # a substitution, or a file descriptor, device or proc path that may lead to a shell
    segs = [s for s in split_segments(line) if s.strip()]
    if not segs:
        return False
    for seg in segs:
        try:
            words = shlex.split(seg, posix=True)
        except ValueError:
            return False
        if not words or re.match(r"^[A-Za-z_]\w*=", words[0]) or "/" in words[0]:
            return False
        first = words[0]
        if first == "tee" and any(re.search(r"[*?\[{$]", w) for w in words[1:]):
            return False  # tee writes to the files it is given: a glob or a variable there can land on /dev/fd or /proc
        if first in HEREDOC_DATA_WORDS or first in extra:
            continue
        if first == "gh" and len(words) > 1 and words[1] in HEREDOC_DATA_GH:
            continue
        if first == "git" and len(words) > 1 and words[1] in GMAIL_GIT_QUOTING and not GMAIL_ENV_INJECT.search(seg):
            continue
        return False
    return True


ECHO_WORDS = frozenset({"echo", "printf"})
# a command that defines a function or alias, or toggles builtins, can make echo or printf run anything: then no line counts as echo prose
ECHO_SHADOW = re.compile(r"\b(?:echo|printf)\s*\(|\bfunction\b|\balias\b|\bunalias\b|\benable\b|\bdeclare\b|\btypeset\b|\bcommand\b|\bbuiltin\b"
                         r"|\beval\b|\bsource\b|\bshopt\b|\btrap\b|\bexec\b|BASH_ALIASES|BASH_FUNC|BASH_ENV|(?:^|[\s;&|({])(?:set|\.)\s")


def _echo_shadowed(cmd):
    """True when the command could redefine echo or printf (a function, alias, eval, source, builtin toggle). Searched on the text with quotes
    and backslashes removed, so al""ias, al\\ias and 'alias' are seen as alias."""
    return bool(ECHO_SHADOW.search(cmd) or ECHO_SHADOW.search(re.sub(r"[\"'\\]", "", cmd)))


def _echo_line_is_data(text):
    """Claude_Commandz issue 235: True when this logical line prints or stores text (echo or printf, with cat, tee ... after a pipe) and
    nothing else: no interpreter, shell, curl or xargs, no substitution, no device or proc path, no redirect target built from a glob
    or a variable. The text such a line prints is prose, not a send."""
    if not _heredoc_line_is_data(text, ECHO_WORDS):
        return False
    for seg in split_segments(text):
        try:
            words = shlex.split(seg, posix=True)
        except ValueError:
            return False
        if words and words[0] == "printf" and any(w == "-v" or w.startswith("-v") and len(w) > 2 and w[2:3].isalpha() for w in words[1:3]):
            return False  # printf -v NAME assigns a variable and prints nothing: its text can be run later
        if words and words[0] in ECHO_WORDS:
            return True
    return False


class ShellBody(str):
    """The body of a heredoc that a shell may run (bash, sh, env bash, sudo sh, ssh, xargs, cat piped to bash ...): besides the row check it is
    checked as one block, the way bash reads it, with quote state carried across rows and a body that cannot be split refused
    (security review 27 Nov: row by row misreads a quote that opens on one row and closes on a later one)."""


NONSHELL_FEEDERS = {"python", "pypy", "node", "nodejs", "deno", "perl", "ruby", "php", "curl", "wget", "jq", "grep", "egrep", "fgrep", "diff", "base64"}


def _line_runs_no_shell(line):
    """True when every command on the logical line is a known tool that does not run its input as shell (an interpreter other than a shell, curl,
    jq, grep ... or a command that only stores or posts text). Anything unclear, a pipe to a shell, env, sudo, ssh, xargs, is not."""
    if re.search(r"\$\(|`|[<>]\(|/dev/|/proc/|>&", line):
        return False  # a substitution, process substitution, device or descriptor path can lead to a shell
    segs = [s for s in split_segments(line) if s.strip()]
    if not segs:
        return False
    for seg in segs:
        try:
            words = shlex.split(seg, posix=True)
        except ValueError:
            return False
        words = [w for w in words if not re.match(r"^[A-Za-z_]\w*=", w)]
        if not words:
            return False
        if "/" in words[0]:
            return False  # a path-qualified name can be any file: not trusted as a known tool
        name = words[0]
        if re.fullmatch(r"(?:python|pypy)[\d.]*", name):
            name = name.rstrip("0123456789.")
        if name not in NONSHELL_FEEDERS and name not in HEREDOC_DATA_WORDS:
            return False
    return True


NAME_CHANGERS = re.compile(r"(?<![\w-])(?:PATH|BASH_ENV|ENV|IFS|LD_PRELOAD|LD_LIBRARY_PATH|SHELLOPTS|BASHOPTS|BASH_CMDS)(?![\w-])"  # as a word anywhere: PATH=, BASH_CMDS[cat]=, printf -v PATH
                           r"|(?<![\w.-])(?:read|mapfile|readarray|printf\s+-v|hash|unset)(?![\w-])"
                           r"|[\w.-]+\s*\(\s*\)|\b(?:coproc|select|case|for|while|until)\b"  # a loop that began earlier could run a later redefinition
                           r"|[(){}]|(?<![\w.-])(?:if|then|else|elif|fi|do|done|esac)(?![\w.-])")
# the last row: a group, subshell, process substitution or compound command that opens before the heredoc can send its output on after the
# body (`{ cat <<'EOF'` ... `} | sh`, `sh < <(cat <<'EOF'` ... `)`), which the line holding the operator does not show; such a command keeps the strict reading


# commands whose arguments are never run as a program (not env, command, exec, time, nohup, xargs, find, sort, tar, git, a shell or an interpreter)
SUBST_SAFE_COMMANDS = {"mkdir", "rmdir", "touch", "rm", "ls", "cat", "echo", "dirname", "basename", "date", "pwd", "cd", "head", "tail", "wc",
                       "grep", "readlink", "realpath", "stat", "test"}


def _names_cannot_change(cmd):
    """True when no part of the command can redefine a command name: no alias, function, eval, source, builtin toggle, search-path or hash change
    (the ECHO_SHADOW list, plus function definitions and the variables that steer name lookup), and no earlier command that names one of the
    storing tools as an argument or a path (cp /bin/sh ~/.local/bin/cat, ln -s ... cat: a file planted under that name). Searched with quotes and
    backslashes removed too."""
    flat = re.sub(r"[\"'\\]", "", cmd)
    plain, flat_plain = SIMPLE_SUBST.sub("$S", cmd), SIMPLE_SUBST.sub("$S", flat)  # a closed plain $(..) is a subshell: it changes no name here
    if _echo_shadowed(cmd) or NAME_CHANGERS.search(plain) or NAME_CHANGERS.search(flat_plain):
        return False
    has_subst = "$(" in cmd or "`" in cmd
    for seg in split_segments(cmd):
        if has_subst:
            # a substitution's output is text the checks above never see: as the command word (also through a variable it was stored in, x=$(..) then
            # $x), or after env/command/time/xargs and the like, it could be hash, export or read, or build the name of a file a redirect or a copy
            # places. So once the command holds one, each segment is trusted only when it is bare assignments, or its command word is a plain name
            # whose arguments cannot be run (SUBST_SAFE_COMMANDS) and it has no redirect aimed at a built name; anything else keeps the strict
            # reading main had for every $( (fifth review and its follow-ups)
            try:
                sw = shlex.split(SIMPLE_SUBST.sub("$S", seg), posix=True)
            except ValueError:
                return False
            while sw and re.match(r"[A-Za-z_]\w*=", sw[0]):
                sw.pop(0)
            if sw and sw[0] not in SUBST_SAFE_COMMANDS:
                return False
            if re.search(r">[^\n]*\$", SIMPLE_SUBST.sub("$S", seg)):
                return False
        try:
            words = shlex.split(re.sub(r"[()]", " ", seg), posix=True)  # a word at the end of a $(..) keeps its ")": ln -sf /bin/bash ~/.local/bin/cat)
        except ValueError:
            return False
        if any(os.path.basename(w) in HEREDOC_DATA_WORDS | {"gh", "git"} for w in words[1:]):
            return False
    return True


def _own_heredoc_segment(text, op):
    """The one command segment of this logical line that holds the heredoc operator `op`, or None when it is not exactly one, or when anything
    follows `op` on the line: a pipe (`cat <<'EOF' | sh` feeds a shell), a descriptor redirect (`2>&3`, which the segment split cuts in two)
    or another command. Only a heredoc that ends its line is read by the command it is written on alone."""
    at = text.find(op)
    if at < 0 or text[at + len(op):].strip():
        return None
    if re.search(r">\||&>|[<>]&(?!\d+-?(?:[\s;&|)]|$)|-)|\\\n", text):
        return None  # >| and >& word and &> are redirects (a descriptor copy such as 2>&1 or >&2 is not one) the segment split cuts in two (bash >| cat <<'EOF'); a backslash-newline joins this line to the one before (bash -s \ then cat <<'EOF')
    segs = [s for s in split_segments(text) if op in s]
    return segs[0] if len(segs) == 1 else None


def split_data_heredocs(cmd):
    """Return (code, bodies, opaque, send_code):
      code      cmd with every heredoc body (and its terminator line) and every comment taken out; this is what is word-split and checked
                for the token folder;
      bodies    the bodies of data heredocs: searched for the token folder's name, never word-split;
      opaque    the text of comments and the bodies of the other heredocs (an interpreter's, a shell's, curl's code): checked row by row on
                their own with no allowlist, so their quotes cannot leak into the command lines around them (issue 234);
      send_code code without the logical lines that only print or store text (echo/printf with cat, tee ...): the text checked for a send address (issue 235).
    Any doubt (an unclosed quote, a substitution, a heredoc with no terminator, $'...') returns (cmd, [], [], cmd), so nothing is loosened."""
    n, i = len(cmd), 0
    code, bodies, line, pending = [], [], [], []
    opaque, send = [], []
    echo_ok = not _echo_shadowed(cmd)
    all_data = [True]  # echo/printf prose is exempt only when EVERY logical line of the command is a data line (security review 27 Nov)
    q = None

    def emit(text, echo_data=False):
        """echo_data: this line is echo/printf prose; it is dropped from send_code at the end, if every line of the command is a data line."""
        code.append(text)
        send.append((text, echo_data))
        if all_data[0] and text.strip() and not _heredoc_line_is_data(text, ECHO_WORDS):
            all_data[0] = False  # a pipe at the end of a line, a { group, a for loop, xargs, eval, a variable: the echo may feed or become code

    while i < n:
        c = cmd[i]
        if q:
            line.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                line.append(cmd[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            line.append(c + cmd[i + 1])
            i += 2
            continue
        if c == "`" or (c == "$" and i + 1 < n and cmd[i + 1] in "('\"{"):
            sub = SIMPLE_SUBST.match(cmd, i) if c == "$" and cmd[i + 1] == "(" else None
            if not sub or not echo_ok:  # (an alias could stand for case or a quote: the strict reading stays when one can be defined)
                return cmd, [], [], cmd
            line.append(sub.group(0))  # kept in the code text, so it is still word-split and checked there
            i = sub.end()
            continue
        if c in "'\"":
            q = c
        elif c == "#" and (not line or (line[-1][-1:] in " \t;&|()\n" and not line[-1].startswith("\\")
                                        and not line[-1].startswith("$(")  # $(..)# is one word: the # is not a comment (third review)
                                        and not (line[-1] == "(" and len(line) > 1 and line[-2][-1:] in tuple("@?*+!")))):
            # (not after an extglob opener @( ?( *( +( !( : there the # is part of a pattern word, and what follows is still a command)
            # a # after an escape pair (\<space>, \<newline>) is part of a word, not a comment; only a real word start begins one
            # (anywhere earlier in the command, not just on this line: a regex or pattern group may run over newlines, and bash drops a
            # backslash-newline pair, so [\<newline>[ is [[)
            # (also $[ ] legacy arithmetic, a =~ regex, and the keywords whose bodies bash reads in their own way: case, coproc, select, function)
            # (and when an alias, eval, source or the like could be defined: an alias value may hold an open quote that bash carries over to the
            # next lines, so a # there is inside a string; echo_ok is false exactly then)
            if not echo_ok or re.search(r"[@?*+!<>]\(|\[\[|\(\(|\$\[|=~|(?<![\w.-])(?:case|coproc|select|function)(?![\w.-])", cmd[:i].replace("\\\n", "")):
                # an extglob group, a [[ ]] test (a regex after =~ may hold a #) or a (( )) arithmetic open on this line: a # there is not
                # necessarily a comment, so do not guess
                return cmd, [], [], cmd
            j = cmd.find("\n", i)
            j = n if j < 0 else j
            if len(cmd[i:j]) - len(cmd[i:j].rstrip("\\")) & 1:
                return cmd, [], [], cmd  # a real comment does not continue on the next line; a wrongly guessed one does, so do not guess
            opaque.append(cmd[i:j])  # a comment is not run, but it is checked as strictly as an interpreter's code, so a wrong guess fails closed
            i = j
            continue
        elif c == "<" and cmd.startswith("<<<", i):
            line.append("<<<")
            i += 3
            continue
        elif c == "<" and cmd.startswith("<<", i):
            m = HEREDOC_OP.match(cmd, i)
            if not m or (m.end() < n and cmd[m.end()] not in " \t\n;&|<>()"):
                return cmd, [], [], cmd  # a word glued to the closing quote (<<'EOF'x) is one terminator word for bash, not EOF
            word = m.group(2) or m.group(3) or m.group(4) or m.group(5)
            if "\\" in word or "$" in word:
                return cmd, [], [], cmd  # bash's quote removal on such a delimiter is not reproduced here
            pending.append((word, bool(m.group(1)), bool(m.group(2) or m.group(3) or m.group(4)), m.group(0)))
            line.append(m.group(0))
            i = m.end()
            continue
        elif c == "\n":
            text = "".join(line) + "\n"
            line = []
            i += 1
            if not pending:
                emit(text, echo_ok and _echo_line_is_data(text))
                continue
            line_data = _heredoc_line_is_data(text)
            line_no_shell = _line_runs_no_shell(text)
            emit(text)
            for word, tabs, quoted, op in pending:
                data_line, no_shell = line_data, line_no_shell
                if not (data_line and no_shell):
                    # Issue 260: a heredoc feeds only the command it is written on, so `cd x && S=y && python3 a.py > f; cat > $S/n <<'EOF'` is
                    # judged by `cat > $S/n <<'EOF'` alone. The other commands on the line are still checked as code. Only when nothing in the
                    # command lines so far (heredoc bodies left out) could make cat (or any name) something else: no alias, function, eval, PATH
                    # or hash change. A name changed on a later line cannot reach a command that already ran.
                    own = _own_heredoc_segment(text, op) if _names_cannot_change("".join(code)) else None
                    if own is not None:
                        data_line = data_line or _heredoc_line_is_data(own)
                        no_shell = no_shell or _line_runs_no_shell(own)
                body, found, term = [], False, ""
                while i < n:
                    j = cmd.find("\n", i)
                    j = n if j < 0 else j
                    row = cmd[i:j]
                    i = min(j + 1, n)
                    if (row.lstrip("\t") if tabs else row) == word:
                        found, term = True, row
                        break
                    body.append(row)
                if not found:
                    return cmd, [], [], cmd
                if quoted and data_line:
                    bodies.append("\n".join(body))
                else:
                    text_body = "\n".join(body)
                    # a body a shell may run (or that bash expands, when the heredoc is not quoted and holds a substitution) is also read as one block
                    runs = not no_shell or (not quoted and ("$(" in text_body or "`" in text_body))
                    opaque.append(ShellBody(text_body) if runs else text_body)
            pending = []
            continue
        line.append(c)
        i += 1
    if q or pending:
        return cmd, [], [], cmd
    last = "".join(line)
    emit(last, echo_ok and _echo_line_is_data(last))
    return "".join(code), bodies, opaque, "".join(t for t, e in send if not (e and all_data[0]))


def _body_rows(body):
    """The rows of an interpreter's heredoc body, each checked on its own: the rows as written, and again with every row that ends in an
    odd number of backslashes joined to the next (bash and a python string join them, so a path or an address can be split across rows)."""
    rows = body.split("\n")
    joined, cur = [], ""
    for row in rows:
        cur += row
        if (len(cur) - len(cur.rstrip("\\"))) % 2 == 1:
            cur = cur[:-1]
            continue
        joined.append(cur)
        cur = ""
    if cur:
        joined.append(cur)
    return rows + joined


def _opaque_use(opaque):
    """"folder", "send" or None for comments and heredoc bodies that are not data: each row on its own (no quote state carries over, so one
    unsplittable row cannot hide the others), and a ShellBody also as one block."""
    for body in opaque:
        for row in _body_rows(body):
            for seg in split_segments(row):
                norm = gmail_norm(seg)
                if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm) or gmail_resolves_into_folder(seg, True, True, False):
                    return "folder"
                if names_send(norm):
                    return "send"
        if _body_path_runs_hit(body):
            return "folder"
        if names_send(gmail_norm(str(body))):  # the whole body at once: a call split over rows (a chain inside parentheses) is one call
            return "send"
        if isinstance(body, ShellBody):
            hit = _shell_body_use(body)
            if hit:
                return hit
    return None


def _body_path_runs_hit(body):
    """True when a path-like run of the whole body, read across rows with its quotes and backslashes removed and a [...] group kept whole
    (commas and spaces inside it included), resolves into the token folder. Whatever runs the body (a shell, or python with os.system on a
    string that spans rows), quote state and word splitting cannot hide a glob from this scan. It refuses only a real hit on the folder."""
    flat = re.sub(r"[\"'\\]", "", str(body))
    for run in re.findall(r"(?:\[[^\]\n]*\]|[^\s;<>()|&`\[])+", flat):
        if ("/" in run or run.startswith(("~", "."))) and gmail_resolves_into_folder(shlex.quote(run)):
            return True
    return False


def _shell_body_use(body):
    """A body that bash may run, read the way bash reads it: its own comments and heredocs split out, quote state carried across rows, a segment
    that cannot be split refused, no allowlist."""
    if _glob_option_reaches_folder(str(body), None, wide=True):  # a body bash runs may set a glob option in a way only the wide reading sees
        return "folder"
    code, bodies, opaque, _send = split_data_heredocs(str(body))
    for b in bodies:
        norm = gmail_norm(b)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm):
            return "folder"
    after_cd, prose_ok = False, not _feeds_shell(code)
    for seg in split_segments(code):
        norm = gmail_norm(seg)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm) or gmail_resolves_into_folder(seg, False, after_cd, prose_ok):
            return "folder"
        after_cd = after_cd or _is_cd_segment(seg)
        if names_send(norm):
            return "send"
    return _opaque_use(opaque)


# Claude_Commandz issue 255: shell options change what a glob matches, and gmail_resolves_into_folder expands globs the way Python does. dotglob lets
# ? * [ match a leading dot, nocaseglob ignores case, extglob adds +(..) @(..) !(..) groups, globstar lets ** cross folders, GLOBIGNORE (any value)
# turns dotglob on and hides . and .. from a match. So with one of them set, a glob word that can start at or above the token folder is refused, since
# the Python expansion no longer says what bash would match. The options are looked for by name in the text with quotes and backslashes removed
# (shop""t is still shopt), and a shopt or -O that is given a word built at run time ($x, a backtick) counts too. Fails closed.
# Second blind review of PR 264: the bare word IFS anywhere counts (for IFS in q, read IFS, printf -v IFS q, ${IFS}, IFS=,); only IFS= and IFS='' (empty) and an
# IFS=value that is a prefix of read, mapfile or readarray (it lasts for that one command and bash splits that command's words before it applies) are ordinary
# Security review of the third round: IFS=q read a b <<< "q/home/*/.." makes /home/*/.. a word the text never shows, so the read prefix is ordinary only for a value
# that holds nothing but separator characters: unquoted only , : | (no letters, no $), in plain quotes only space , : | ;, and the \t \n \r escapes only inside $'..'
# (a tab written IFS=$'\t' reads as $t in the text with quotes removed, which is then a variable and counts as a set IFS: a harmless extra look, never a miss)
IFS_SET = re.compile(r"""(?<![\w-])IFS(?![\w-])(?!=(?:$|[\s;&|)]|''(?=$|[\s;&|)])|""(?=$|[\s;&|)]))|=(?:\$'(?:\\[tnr]|[ ,:|;])*'|'[ ,:|;]*'|"[ ,:|;]*"|[,:|]*)[ \t]+(?:read|mapfile|readarray)\b)""")
# a variable name built at run time in a declare, typeset, local, export, readonly or env assignment, or in printf -v or read: declare ${x}IGNORE=x
BUILT_NAME = r"(?<![\w.-])(?:(?:declare|typeset|local|export|readonly|env)\b(?:[ \t]+-\S+)*[ \t]+[\w]*[$`{][^\s=;&|]*=|printf[ \t]+-v[ \t]*\S*[$`{]|(?:read|mapfile|readarray)\b(?:[ \t]+-\S+)*[ \t]+\S*[$`{])"
GLOB_OPTION = re.compile("|".join((r"(?<![\w-])(?:dotglob|nocaseglob|extglob|globstar|GLOBIGNORE|BASHOPTS|globdots|glob_dots|extendedglob|extended_glob|kshglob|ksh_glob|setopt|unsetopt|globskipdots|globasciiranges)(?![\w-])",
                                   # fifth blind review: zsh reads option names without regard to case or underscores (GLOB_DOTS, NO_CASE_GLOB), and globasciiranges off
                                   # makes [A-Z] follow the locale's order
                                   r"(?i:(?<![\w-])(?:no_?)?(?:glob_?dots|case_?glob|extended_?glob|ksh_?glob|glob_?skip_?dots)(?![\w-]))",
                                   IFS_SET.pattern,  # an IFS set to something (or ${IFS} used as a separator) splits words on other characters; while IFS= read -r is ordinary
                                   BUILT_NAME,
                                   r"(?<![\w.-])shopt\b[^;&|\n]*[$`{*?\[]",
                                   r"(?<![\w.-])(?:bash|sh|zsh|ksh)\b[^;&|\n]*[ \t]-[A-Za-z]*[Oo][ \t]*[^\s;&|]*[$`{*?\[]")))
EXT_GLOB_GROUP = re.compile(r"[?*+@!]\(")
# In the text with quotes and backslashes removed, a tab written IFS=$'\t' reads as IFS=$t; there (and only there) a $ and t, n or r are the escape, so that
# ordinary line is not flagged. The raw text is searched with the strict pattern, so a real IFS=$t or IFS=t is still caught.
_TAB_SPELLING = "|[,:|]*)[ \\t]+"
GLOB_OPTION_BARE = re.compile(GLOB_OPTION.pattern.replace(_TAB_SPELLING, "|[,:|]*|\\$[tnr]+)[ \\t]+"))
IFS_SET_BARE = re.compile(IFS_SET.pattern.replace(_TAB_SPELLING, "|[,:|]*|\\$[tnr]+)[ \\t]+"))
assert GLOB_OPTION_BARE.pattern != GLOB_OPTION.pattern and IFS_SET_BARE.pattern != IFS_SET.pattern
# What can set a glob option without naming it: a command word built at run time ($s -s ..., `x` ..., sho{p,q}t), eval, source, a dot-source,
# BASH_ENV or an rc file named in the text (.bashrc, --rcfile; an rc file an earlier call already changed is not seen: filed on issue 265), a function or
# alias of the name shopt. A test bracket, a group brace, a for-loop word list, export and PATH= cannot.
OPTION_SETTER = re.compile(r"(?:^|[;&|\n){!]|(?<![\w.-])\(|(?<![\w.-])(?:then|do|else|elif|if|while|until)\(?|(?<![\w.-])time(?:[ \t]+-p)?(?:[ \t]+--)?[ \t]*\(?|(?<![\w.-])coproc(?:[ \t]+\w+)?[ \t]*\(?)[ \t]*"
                           r"(?:(?:[A-Za-z_]\w*=\S*|\d*[<>]+&?[ \t]*\S+)[ \t]+)*[\w.-]*(?:[$`]|\w\{)"
                           r"|(?<![\w.-])(?:--rcfile|--init-file)(?![\w-])|[\w.-]+[ \t]*\([ \t]*\)")


def _mask_substitutions(text):
    """text with every $(..) span (nested parentheses counted) and every backtick span replaced by $S, so a word that holds one is not cut at its spaces."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("$(", i):
            depth, j = 0, i + 1
            while j < n:
                depth += (text[j] == "(") - (text[j] == ")")
                if depth == 0:
                    break
                j += 1
            out.append("$S")
            i = j + 1
        elif text[i] == "`":
            j = text.find("`", i + 1)
            out.append("$S")
            i = n if j < 0 else j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _could_be_dotdot(c):
    """True for a path part that is only extglob groups and bracket expressions (no letter or digit outside them) and could match ..: an inside that holds a
    dot, a * ? or [, or a group that starts with !. A part with a literal letter outside (!(keep).log) is an ordinary pattern."""
    stripped = c
    inner = []
    for _ in range(200):
        m = re.search(r"[?*+@!]\(([^()]*)\)|\[([^\[\]]*)\]", stripped)
        if not m:
            break
        inner.append((m.group(1) if m.group(1) is not None else m.group(2), m.group(0).startswith("!(")))
        stripped = stripped[:m.start()] + stripped[m.end():]
    else:
        return True  # more groups than the loop reads: refused
    if not inner:
        return False  # (a piece cut from a word at a ( or | has no whole group; the whole word is read as well)
    if re.search(r"[()\[\]]", stripped):
        return True  # an opener or closer left over that was not understood
    if re.search(r"[A-Za-z0-9_-]", stripped):
        return False
    if "." in stripped:
        return True  # dots beside a group that can be empty (.?(x). , .*(a).) are .. when the group matches nothing
    # a bracket or group body is safe only when it is plain letters and digits (and ranges between them, none of which can include a dot); anything else, a !, ^, a
    # class such as [:alpha:], a | list holding a dot or glob character, may match ..
    return any(neg or not re.fullmatch(r"[A-Za-z0-9_|]*(?:[A-Za-z0-9_]-[A-Za-z0-9_][A-Za-z0-9_|]*)*", body) for body, neg in inner)


_LITERAL_VAR = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def _literal_path_vars(bare):
    """Variables that the command assigns exactly once, to a literal absolute path (no glob character, no expansion, no ~, no . or .. part, not HOME),
    and never changes another way (+=, read, for, mapfile, printf -v, declare -n, unset, export -n). bare is the command without its quotes."""
    values = {}
    counts = {}
    for m in re.finditer(r"(?:^|[\s;&|(])(?:(?:export|declare|local|readonly|typeset)\s+(?:-\w+\s+)*)?([A-Za-z_]\w*)=([^\s;&|<>()]*)", bare):
        name = m.group(1)
        counts[name] = counts.get(name, 0) + 1
        values[name] = m.group(2)
    result = {}
    for name, value in values.items():
        if counts[name] != 1 or name == "HOME" or not value.startswith("/") or re.search(r"[*?\[\]{}$`~\\!]", value):
            continue
        if any(part in (".", "..") for part in value.split("/")):
            continue
        n = re.escape(name)
        if re.search(rf"(?<!\w){n}\+=|\b(?:read|mapfile|readarray|getopts|for|select|unset)\b[^;&|\n]*(?<!\w){n}(?!\w)|-v\s*{n}(?!\w)|declare\s+-\w*n|typeset\s+-\w*n|export\s+-\w*n", bare):
            continue
        result[name] = value
    return result


def _glob_option_reaches_folder(cmd, cwd=None, wide=False):
    """True when cmd sets (or could set) a glob-changing shell option and holds a glob word whose fixed front (the folders before the first glob
    character) is the token folder, one of its parents or a folder inside it. A relative glob word starts at the working folder: the payload's
    cwd, or anything at all once the command has a cd, pushd or popd. A word that holds $ or .., or a ~+ ~- ~N form, cannot be resolved from the
    text and is refused. The option counts as possibly set when the command word is a variable, a function or alias could stand in for shopt
    (OPTION_SETTER, ALIAS_DEFINER; looked at only with wide, on the command text without its data heredoc bodies), a shopt or
    bash -O argument is built at run time, or a variable name is (BUILT_NAME). One budget covers the whole call."""
    bare = re.sub(r"[\"'\\]", "", cmd)
    decoded = re.sub(r"[\"'\\]", "", gmail_norm(cmd))  # gmail_norm also decodes $'\x64otglob' words
    # an option name spelled around an expansion that is empty (GLOB${e}IGNORE=x, dot${e}glob) is read with every expansion removed too
    # (taken from the raw text, so GLOB"$e"IGNORE is GLOBIGNORE and not the variable $eIGNORE)
    emptied = re.sub(r"[\"'\\]", "", re.sub(r"\$\{[^{}]*\}|\$\w+", "", cmd))
    # zsh ignores underscores anywhere in an option name (G_LOB_DOTS), so the text without them is read too (no zsh is installed on our machines; this is cheap insurance)
    named = bool(GLOB_OPTION_BARE.search(bare) or GLOB_OPTION_BARE.search(bare.replace("_", "")) or GLOB_OPTION.search(decoded) or GLOB_OPTION_BARE.search(emptied) or GLOB_OPTION.search(cmd))
    if not (named or (wide and (OPTION_SETTER.search(bare) or OPTION_SETTER.search(decoded) or ALIAS_DEFINER.search(bare) or ALIAS_DEFINER.search(decoded)))):
        return False
    possible_only = not named  # an option that is only possible (a variable as the command word), nothing in the text sets one
    ifs = bool(IFS_SET.search(cmd) or IFS_SET_BARE.search(bare))  # any character can split a word then: every /... or ~... inside a word is a start of its own
    skipdots = True  # globskipdots can be turned off by a name built at run time ($eskipdots, a brace, a quote shift): while any glob option is named or possible, a .* or .? component may match ..
    folder = os.path.realpath(os.path.expanduser("~/.config/afa/gmail"))

    seen = {}

    def reaches(directory):  # the folder, below it, or any folder above it (/, /home, the home folder, ~/.config, ~/.config/afa); remembered per folder
        if directory not in seen:
            real = os.path.realpath(directory).rstrip(os.sep)
            seen[directory] = real == folder or folder.startswith(real + os.sep) or real.startswith(folder + os.sep)
        return seen[directory]

    # cd, pushd, popd, and the commands that start another process in another working folder (env -C, sudo -D, ssh, su -, runuser -l, nsenter -w ..):
    # a relative glob word then starts somewhere the payload's cwd does not say
    # (fifth blind review: find -execdir, chdir and cwd= in an interpreter's code, tmux and screen start a process in another folder too)
    moves = bool(re.search(r"(?<![\w.-])(?:cd|pushd|popd|env|sudo|doas|su|runuser|ssh|nsenter|chroot|systemd-run|setpriv|machinectl|tmux|screen)(?![\w-])|--chdir|--cwd|-execdir|chdir|cwd", bare))
    try:
        base = os.path.realpath(cwd if isinstance(cwd, str) and cwd else os.getcwd())
    except OSError:
        return True
    home_trusted = not (re.search(r"(?<![$\w{])HOME(?!\w)", bare) or re.search(BUILT_NAME, bare))  # HOME= or export HOME or a built name: $HOME is not the folder it was
    budget = 1000000  # characters of words and expanded forms for the whole call: a call that costs more than this is refused, never left to run out the hook's time
    # a $(..) or backtick span is cut where it holds spaces, so it is also read as one marker word ($S): $(echo /home)/*/?config/.. is $S/*/?config/..
    masked = _mask_substitutions(bare)
    literal_vars = _literal_path_vars(bare)
    # (a word of the masked text may carry the marker $S for a whole $(..): that is not the variable S, so a word that is only in the masked text never has its
    # variables read as literal paths)
    bare_toks = re.split(r"[\s;&|<>`]+", bare)
    bare_set = set(bare_toks)
    for tok, from_masked in [(t, False) for t in bare_toks] + ([(t, t not in bare_set) for t in re.split(r"[\s;&|<>`]+", masked)] if masked != bare else []):
        if len(tok) > 2000:
            return True  # one very long word is refused rather than expanded in the hook's time
        # a word after an = or a ( is a word of its own: x=~/?config/..., x=(~/?config/...), declare -a x=(...)
        # (a path list x=a:~/... is split at its colons too: bash expands the ~ after each one in an assignment)
        # with IFS set, bash splits the result of an expansion or of an assignment's value on any character, so /home/... after a q is a word of its own
        # (literal text on the command line is never split by IFS)
        starts = {i + 1 for i, ch in enumerate(tok) if ch in "=(:,"} | ({i for i, ch in enumerate(tok) if ch in "/~"} if ifs else set())
        budget -= len(tok) * (len(starts) + 1)
        if budget < 0:
            return True
        for word in {tok} | {tok[i:] for i in starts}:
            if home_trusted:
                word = re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", word)  # "$HOME"/repos/*/x is the home folder, as ~ is (unless the command names HOME in some other way)
            # a variable that is assigned once in this command to a literal absolute path ($S/body.md after S=/tmp/x/scratchpad, the hub's body command, PR 261's test)
            # is read as that path; every other word with a $ or a backtick counts as it did (security review of the commit after 36d3160)
            substituted = False
            # (a word that also holds a glob is not read this way: its glob can match what the variable's value does not say)
            if "$" in word and "`" not in word and literal_vars and not from_masked and not (GMAIL_GLOB.search(word) or EXT_GLOB_GROUP.search(word)):
                swapped = _LITERAL_VAR.sub(lambda m: literal_vars.get(m.group(1) or m.group(2), m.group(0)), word)
                if swapped != word and "$" not in swapped:
                    word, substituted = swapped, True
            # a word with a $ or a backtick counts too (issue 255 review): bash globs an unquoted expansion, so /home/$b is a glob when b holds one
            if not word or not (substituted or GMAIL_GLOB.search(word) or EXT_GLOB_GROUP.search(word) or "$" in word or "`" in word):
                continue
            comps = word.split("/")
            # sixth review: an extglob group can match .. (@(..)/@(..)/?config/..), so a part that holds .. beside a ( or ) counts as .. itself
            if any(c == ".." or (".." in c and ("(" in c or ")" in c)) for c in comps):
                return True
            # seventh review: so can any part made only of groups and brackets whose insides hold a dot, a glob character or a leading ! ([.][.], ?(.)?(.), !(x))
            if len(comps) > 1 and any(_could_be_dotdot(c) for c in comps):
                return True
            # (a word with one part only (.[] in a jq filter, .* alone) names no path through ..: it is not read here, the false refusal of the jq filter test of PR 261)
            if skipdots and len(comps) > 1 and any(c.startswith(".") and GMAIL_GLOB.search(c) for c in comps):
                return True  # with globskipdots off a .* or .? component anywhere matches ..
            # a variable can hold the home folder, any parent of the token folder or ..: $d/*/?config/... and /tmp/$u/home/*/?config/... are refused while an
            # option is named, a glob after a variable anywhere in the front; only a glob in the last part ("$OUT"/*.tmp, "$d"/*.json) is the ordinary loop and passes
            var_at = next((i for i, c in enumerate(comps) if "$" in c or "`" in c), None)  # (one pass: a long word is not checked part by part against every later part)
            if var_at is not None and any(GMAIL_GLOB.search(d) or EXT_GLOB_GROUP.search(d) for d in comps[var_at + 1:-1]):
                return True
            # the glob may come from a variable's value instead (p='*/?config/a?a/g?ail/token.json'; cat $H/$p): a relative word with nothing fixed in front and a
            # glob before its last part, taken from an assignment or a read, is a start nothing here can place
            if ((word != tok or re.search(r"\b(?:read|mapfile|readarray)\b|<<<", bare)) and re.search(r"\$[\w{@*(]", bare)
                    and (GMAIL_GLOB.search(comps[0]) or EXT_GLOB_GROUP.search(comps[0])) and len(comps) > 1):
                return True
            expanded = _brace_expand(word, BRACE_LIMIT)
            if expanded is None:
                return True
            for one in expanded:
                budget -= len(one) + 1
                if budget < 0:
                    return True
                one = os.path.expanduser(gmail_norm(one))
                if one.startswith("~"):
                    return True  # ~+ ~- ~N and ~name: a folder this text does not say
                front = []
                for comp in one.split("/"):
                    if GMAIL_GLOB.search(comp) or EXT_GLOB_GROUP.search(comp) or "$" in comp or "`" in comp:
                        break
                    front.append(comp)
                fixed = "/".join(front)
                if os.path.isabs(one):
                    if reaches(fixed or "/"):
                        return True
                elif (moves and not (possible_only and "/" not in one)) or reaches(os.path.join(base, fixed)):
                    # (when no option is named, only possible, a word of one part such as the python s[:i] of a heredoc body after a cd is not taken for a path: the false
                    # refusal of the hub's body command, PR 261's test; the gap is noted on issue 265)
                    return True
    return False


def gmail_credential_use(cmd, cwd=None):
    """Return "folder" or "send" when cmd would touch the mail token's folder or names a Gmail send address; else None."""
    if _glob_option_reaches_folder(cmd, cwd):
        return "folder"
    whole = gmail_norm(cmd)
    if re.search(r"gmail_api\.py|redact_view\.py", whole) and GMAIL_ENV_INJECT.search(whole):
        return "folder"  # the two scripts that may hold the token must not run with variables that load other code
    cmd, bodies, opaque, send_cmd = split_data_heredocs(cmd)
    if _glob_option_reaches_folder(cmd, cwd, wide=True):
        return "folder"
    for body in bodies:  # a data body is not split into words, but one that names the token folder is still refused
        norm = gmail_norm(body)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm):
            return "folder"
    after_cd, prose_ok = False, not _feeds_shell(cmd)
    for seg in split_segments(cmd):
        norm = gmail_norm(seg)
        if (GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm)
                or gmail_resolves_into_folder(seg, False, after_cd, prose_ok)) and not gmail_segment_ok(seg):
            return "folder"
        after_cd = after_cd or _is_cd_segment(seg)
    bare = re.sub(r"[\"'\\]", "", cmd)
    if ALIAS_DEFINER.search(bare) or ALIAS_DEFINER.search(cmd) or _echo_shadowed(cmd) or VARIABLE_COMMAND_WORD.search(bare) \
            or NAME_SHADOW.search(bare) or QUOTE_SHIFTER.search(cmd):
        # Claude_Commandz issue 241: an alias (or BASH_ALIASES, eval, source, a startup file) can hold an open quote, so bash's quote state
        # then differs from the one split_segments read from the text and a quoted-looking glob runs as a command. Such a command is read
        # quote-blind: cut at every ; & | and newline whatever the quotes, each piece checked as a row of an interpreter's heredoc is. There is
        # no allowlist here (ls, the mail tool, the masked viewer): an alias can make any of those words run something else, so a piece that
        # names or globs into the token folder is refused. That fails closed: set -e; ls ~/.config/afa/gmail, or the mail tool run with the
        # folder named and the word eval, source, set or command anywhere in the command, is refused too.
        # Issues 237 and 236: a command substitution inside double quotes ("$(cat ...)", a backtick) runs as a command but reads as part of one
        # quoted word, and an ANSI-C or locale quoted word ($'..', $"..") ends at a different quote than the text reader thinks. Both are read
        # the same way, and the pieces are also cut at ( ) and the backtick so a substitution's inside is a piece of its own.
        for seg in re.split(r"[;&|\n]", cmd) + re.split(r"[;&|\n()`]", cmd):
            norm = gmail_norm(seg)
            if (GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm)
                    or gmail_resolves_into_folder(seg, True, True, False)):
                return "folder"
    hit = _opaque_use(opaque)  # a comment or an interpreter's code: no allowlist and no quoted-mention exemption, any hit is refused
    if hit:
        return hit
    for seg in split_segments(send_cmd):
        if names_send(gmail_norm(seg)):
            words = gmail_words(seg) or []
            quoted = bool(words and os.path.basename(words[0]) in GMAIL_QUOTE_WORDS and "$(" not in seg and "`" not in seg)
            if quoted and os.path.basename(words[0]) == "git":  # git runs aliases and config: only a read-only subcommand counts, never with -c or GIT_ variables
                quoted = len(words) > 1 and words[1] in GMAIL_GIT_QUOTING and not GMAIL_ENV_INJECT.search(seg)
            if quoted:
                continue
            return "send"
    return None


def gmail_path_hit(path, content_search=False):
    """A Read or Grep path inside the token folder (real path, symlinks followed), or a content search that would reach it."""
    if not isinstance(path, str) or not path:
        return False
    norm = gmail_norm(path)
    real = os.path.realpath(os.path.expanduser(norm))
    folder = os.path.realpath(os.path.expanduser("~/.config/afa/gmail"))
    if real == folder or real.startswith(folder + os.sep) or GMAIL_DIR.search(norm):
        return True
    if content_search:
        for up in (os.path.dirname(folder), os.path.dirname(os.path.dirname(folder))):
            if real == up:
                return True
        return bool(re.search(r"\.config/afa/?$|\.config/?$", norm))
    return False


def blank_quotes(seg):
    seg = re.sub(r"'[^']*'", lambda m: " " * len(m.group(0)), seg)
    return re.sub(r'"[^"]*"', lambda m: m.group(0) if ("$(" in m.group(0) or "`" in m.group(0))
                  else " " * len(m.group(0)), seg)


PROC_CMDLINE = re.compile(r"/proc/(?:[^/\s]+/)+cmdline(?:\b|$)")
PROCESS_HELPER = os.path.expanduser("~/.claude/scripts/ps_redacted.sh")


def _process_segments(text):
    """Shell command segments, including command substitutions, with quoted separators ignored."""
    segments, current = [], []
    quote = None
    escaped = False
    i = 0
    while i < len(text):
        c = text[i]
        if escaped:
            current.append(c)
            escaped = False
        elif c == "\\" and quote != "'":
            current.append(c)
            escaped = True
        elif quote:
            if c == quote:
                quote = None
            current.append(c)
            if c == "$" and quote == '"' and text[i + 1:i + 2] == "(":
                segments.append("".join(current[:-1]))
                current = []
                end = _subshell_end(text, i + 1)
                segments.extend(_process_segments(text[i + 2:end]))
                i = end
        elif c in "\"'":
            quote = c
            current.append(c)
        elif c == "$" and text[i + 1:i + 2] == "(":
            segments.append("".join(current))
            current = []
            end = _subshell_end(text, i + 1)
            segments.extend(_process_segments(text[i + 2:end]))
            i = end
        elif c in ";&|\n":
            segments.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    segments.append("".join(current))
    return segments


def _subshell_end(text, opening):
    depth, quote, escaped = 1, None, False
    i = opening + 1
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
        elif c == "\\" and quote != "'":
            escaped = True
        elif quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _process_command(segment):
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    i = 0
    wrappers = {"sudo", "command", "exec", "env", "nice", "nohup", "stdbuf", "timeout", "time"}
    while i < len(tokens):
        word = tokens[i]
        base = os.path.basename(word)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            i += 1
            continue
        if base not in wrappers:
            break
        i += 1
        if base == "env":
            while i < len(tokens) and tokens[i].startswith("-"):
                option = tokens[i]
                i += 1
                if option in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"} and i < len(tokens):
                    i += 1
            if i < len(tokens) and tokens[i] == "--":
                i += 1
        elif base == "sudo":
            while i < len(tokens) and tokens[i].startswith("-"):
                option = tokens[i]
                i += 1
                if option in {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
                              "-C", "--close-from", "-D", "--chdir", "-R", "--chroot",
                              "-T", "--command-timeout"} and i < len(tokens):
                    i += 1
        elif base == "timeout":
            while i < len(tokens):
                option = tokens[i]
                if option in {"-s", "--signal", "-k", "--kill-after"} and i + 1 < len(tokens):
                    i += 2
                elif option.startswith("-") or re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", option):
                    i += 1
                else:
                    break
        elif base == "nice":
            if i < len(tokens) and tokens[i] in {"-n", "--adjustment"}:
                i += 1
                if i < len(tokens):
                    i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
        elif base == "time":
            while i < len(tokens):
                option = tokens[i]
                if option in {"-f", "--format", "-o", "--output"} and i + 1 < len(tokens):
                    i += 2
                elif option.startswith("-"):
                    i += 1
                else:
                    break
        else:
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
    return tokens, i


def _lists_process_args(tokens, i):
    if i >= len(tokens):
        return False
    name = os.path.basename(tokens[i])
    args = tokens[i + 1:]
    if name in {"bash", "sh", "dash"}:
        for j, arg in enumerate(args[:-1]):
            if arg == "-c" or (arg.startswith("-") and "c" in arg[1:]):
                return bool(_process_listing(args[j + 1]))
    if name == "ps":
        for j, arg in enumerate(args):
            if arg in {"aux", "au", "ax", "-aux"} or re.fullmatch(r"-[A-Za-z]*[fF][A-Za-z]*", arg):
                return True
            if arg == "--format" and j + 1 < len(args):
                columns = args[j + 1]
            elif arg.startswith("--format="):
                columns = arg.split("=", 1)[1]
            elif arg == "-o" and j + 1 < len(args):
                columns = args[j + 1]
            elif re.match(r"^-[A-Za-z]*o", arg):
                columns = arg[arg.index("o") + 1:]
                if not columns and j + 1 < len(args):
                    columns = args[j + 1]
            else:
                continue
            if re.search(r"(?:^|,)\s*(?:args?|cmd|command)(?:\s*=|,|$)", columns, re.I):
                return True
    elif name == "pgrep":
        if any(arg == "--all" or arg == "--list-full" or
               (arg.startswith("-") and not arg.startswith("--") and "a" in arg[1:])
               for arg in args):
            return True
    elif name == "top" and any(arg in {"-c", "--command"} for arg in args):
        return True

    paths = [arg for arg in tokens[i + 1:] if PROC_CMDLINE.search(arg)]
    if paths:
        if name in {"grep", "rg"}:
            positional = [arg for arg in args if not arg.startswith("-")]
            return any(PROC_CMDLINE.search(arg) for arg in positional[1:])
        if name in {"echo", "printf", "git", "find", "ls", "stat", "test"}:
            return False
        return True
    return False


def _process_listing(segment):
    tokens, i = _process_command(segment)
    return _lists_process_args(tokens, i)


def process_listing_in(cmd):
    return any(_process_listing(segment) for segment in _process_segments(cmd))


def is_viewer_segment(seg):
    """True when the segment RUNS the redacting viewer: python (a safe option or none) on the real ~/.claude/scripts/redact_view.py,
    with no shell syntax in it. The viewer's name elsewhere in a command (an echo, a comment, another command, a copy of the
    script in another folder) does not exempt anything."""
    if re.search(r"[<>(){}$`\\#]", seg):
        return False
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        return False
    if not words or not re.fullmatch(r"python[\d.]*", os.path.basename(words[0])) or "=" in words[0]:
        return False
    i = 1
    while i < len(words) and words[i] in ("-I", "-u", "-B", "-E", "-s", "-S", "-O", "-OO"):
        i += 1
    if i >= len(words) or words[i].startswith("-"):
        return False
    if not (words[i].startswith("/") or words[i].startswith("~/")):
        return False  # a relative path means the folder the shell is in then, which an earlier "cd" may have changed
    script = os.path.realpath(os.path.expanduser(words[i]))
    return script == os.path.realpath(os.path.expanduser("~/.claude/scripts/redact_view.py"))


NAMES_PRODUCERS = {"ls", "find"}  # print file names and never a file's content (find only without an action that runs or writes)
FIND_ACTIONS = re.compile(r"^-(?:exec|execdir|ok|okdir|fprint\w*|fls|delete)$")
NAME_GREPS = {"grep", "egrep", "fgrep", "rg"}
NAME_GREP_FLAGS = re.compile(r"^-[EFGivwxonhHsq]+$|^--(?:ignore-case|extended-regexp|fixed-strings|word-regexp|line-regexp|invert-match|only-matching|quiet|no-filename|with-filename)$")


def _names_producer(seg):
    """Claude_Commandz issue 267: True for a plain ls or find that only prints file names (no substitution, redirect or find action)."""
    if re.search(r"[$`<>(){}\\#\n]", seg):  # # and a newline can hide a quote that makes the splitter read a later command as part of this one
        return False
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        return False
    if not words or words[0] not in NAMES_PRODUCERS or "=" in words[0]:
        return False
    return not (words[0] == "find" and any(FIND_ACTIONS.match(w) for w in words[1:]))


def _names_pattern_grep(seg):
    """Issue 267: True for a grep or rg with flags from a short safe list and exactly one other word, its pattern: it reads its standard
    input and no file, so a credential file's name in the pattern is a search for a name and not a read. -r, -f, -e, a file operand,
    a second word, a substitution and a redirect are not in the list, so any of them leaves the segment checked as before.
    The pattern may hold $ and \\ only inside single quotes (the shell reads nothing there); anywhere else they refuse."""
    q = None
    for c in seg:  # quote-aware: nothing is active inside single quotes; a $, backtick, redirect, bracket or backslash anywhere else refuses
        if q == "'":
            q = None if c == "'" else q
        elif c == "'" and q is None:
            q = "'"
        elif c == '"':
            q = None if q == '"' else '"'
        elif c in "$`<>(){}\\":
            return False
        elif q is None and c in "*?[~=":
            return False  # an unquoted glob, ~ or = word is expanded by the shell into file names that grep would then open
    if q:
        return False
    try:
        words = shlex.split(seg, posix=True)
    except ValueError:
        return False
    if len(words) < 2 or words[0] not in NAME_GREPS:
        return False
    flags = [w for w in words[1:] if w.startswith("-")]
    rest = [w for w in words[1:] if not w.startswith("-")]
    if len(rest) != 1 or rest[0] == "" or not all(NAME_GREP_FLAGS.match(w) for w in flags):
        return False
    return True


def prints_sensitive(cmd):
    all_segs, all_ops = split_segments_ops(cmd)
    # The viewer's exemption holds only when EVERY part of the command is a run of the viewer: any other part (a cd, an
    # export, an alias, a function, a changed PATH) could change what "python" or the script is, and a list of such
    # words is never complete. Mixed with anything else, the whole command is checked as if there were no viewer.
    segs = [] if all(is_viewer_segment(seg) for seg in all_segs) else all_segs
    rest = "\n".join(segs)  # the command with the viewer's own segments taken out
    whole = sensitive_in(rest)
    if not whole:
        return None
    if INTERP.search(blank_quotes(rest)) and INTERP_EVAL.search(rest):
        return whole
    for k, seg in enumerate(segs):
        label = sensitive_in(seg)
        if not label:
            continue
        # issue 267: a pattern-only grep fed by one plain ls or find over a single pipe searches names, it does not read the credential file
        # only when the whole command is exactly that one pipe of two commands: an earlier function, alias, PATH change or other
        # segment could make ls or grep run something else (security review of PR 268)
        if (k > 0 and all_ops[k] == "|" and len([s for s in all_segs if s.strip()]) == 2
                and _names_producer(segs[k - 1]) and _names_pattern_grep(seg)):
            continue
        scan = seg if SHELL_C.search(seg) else blank_quotes(seg)
        if GIT_PRINT.search(scan) or REDIR_IN.search(scan) or "redact_view.py" in seg:
            return label  # (a segment that names the viewer but is not the real one is a lookalike script)
        verbs = set(VERB.findall(scan))
        if SHELL_C.search(seg) and verbs:
            return label
        if verbs - {"grep", "rg"}:
            return label
        if verbs and not GREP_QUIET.search(scan):
            return label
    return None


def evaluate(payload):
    """Return (rule, reason) to block, or None to allow."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input", {}), dict):
        raise ValueError("unexpected payload shape")
    tool, ti = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Bash" and isinstance(ti.get("command"), str):
        cmd = ti["command"]
        if process_listing_in(cmd):
            return "P", ("secret-guard: this would print process command lines. Use the redacting helper "
                         "%s instead." % PROCESS_HELPER)
        s = find_secret(cmd)
        if s:
            return "A", ("secret-guard: rule A, this command contains a literal secret (%s). "
                         "Never put secrets inline in commands; read them from a file or env var "
                         "inside a script, and reference them as $VAR." % s)
        g = gmail_credential_use(cmd, payload.get("cwd"))
        if g == "send":
            return "G", ("secret-guard: rule G, this command names a Gmail send address. The mail tool makes drafts and never "
                         "sends; the owner sends from Gmail's own page.")
        if g:
            return "G", ("secret-guard: rule G, this would read or copy the mail token's folder. That token can send mail. "
                         "Use python3 ~/.claude/scripts/gmail_api.py, or inspect a file with redact_view.py.")
        p = prints_sensitive(cmd)
        if p:
            return "B", ("secret-guard: this would print a credential-bearing file (%s). %s" % (p, HINT))
        for f in reader_targets(cmd, payload.get("cwd")):
            where = secret_in_file(f)
            if where:
                return "E", RULE_E % (f, where)
    elif tool == "Read" and isinstance(ti.get("file_path"), str):
        if gmail_path_hit(ti["file_path"]):
            return "G", ("secret-guard: rule G, Read of the mail token's folder is blocked. That token can send mail. "
                         "Inspect a file with python3 ~/.claude/scripts/redact_view.py.")
        p = sensitive_in(ti["file_path"])
        if p:
            return "C", ("secret-guard: Read of a credential-bearing file (%s) is blocked. %s" % (p, HINT))
        where = secret_in_file(ti["file_path"])
        if where:
            return "E", RULE_E % (ti["file_path"], where)
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        # What a session writes: a token-shaped value (a known key prefix, a private key, a JWT, a webhook address) is refused.
        # The name=value rule is not used here: code and tests set names like token= all the time.
        path = ti.get("file_path") or ti.get("notebook_path")
        if isinstance(path, str) and gmail_path_hit(path):
            return "G", ("secret-guard: rule G, writing into the mail token's folder is blocked. That token can send mail.")
        texts = [ti.get("content"), ti.get("new_string"), ti.get("new_source")]
        texts += [e.get("new_string") for e in ti.get("edits") or [] if isinstance(e, dict)]
        for t in texts:
            if isinstance(t, str):
                label = find_token(t)
                if label:
                    return "F", ("secret-guard: rule F, this %s would write what looks like a real secret (%s) into a file. "
                                 "Put the value in an environment variable or a private config file and reference it by name; "
                                 "for a test, build a made-up value at run time." % (tool, label))
    elif tool == "Grep":
        if ti.get("output_mode") == "content" and (gmail_path_hit(ti.get("path"), content_search=True)
                                                    or GMAIL_DIR.search(gmail_norm(str(ti.get("glob") or "")))):
            return "G", ("secret-guard: rule G, Grep content output on the mail token's folder is blocked. That token can "
                         "send mail. Use output_mode files_with_matches or count.")
        p = sensitive_in("%s %s" % (ti.get("path") or "", ti.get("glob") or ""))
        if p and ti.get("output_mode") == "content":
            return "D", ("secret-guard: Grep content output on a credential-bearing file (%s) is "
                         "blocked (use output_mode files_with_matches or count). %s" % (p, HINT))
        if ti.get("output_mode") == "content" and isinstance(ti.get("path"), str):
            where = secret_in_file(ti["path"])
            if where:
                return "E", RULE_E % (ti["path"], where)
    return None


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as e:
        log_error("secret-guard internal error: %s: %s" % (type(e).__name__, e))
        return 0  # nothing that names a tool arrived, so there is nothing to judge
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input", {}), dict):
        log_error("secret-guard internal error: unexpected payload shape")
        return 0
    try:
        res = evaluate(payload)
    except Exception as e:
        # A call the guard could not read is refused, never allowed: a command built to make the reading crash must not get past it
        # (workspace issue 93). The cause goes to the log, never the command text.
        log_error("secret-guard internal error: %s: %s" % (type(e).__name__, e))
        print("secret-guard: this call could not be read safely, so it is refused. Make the command simpler, or read the file "
              "with python3 ~/.claude/scripts/redact_view.py. The cause is in ~/.local/share/claude-hooks/secret-guard.log.", file=sys.stderr)
        return 2
    if res:
        print(res[1], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
