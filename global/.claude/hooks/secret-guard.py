#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block literal secrets and credential-file dumps.

stdin: {"tool_name": ..., "tool_input": {...}}
block: reason on stderr, exit 2.  allow: exit 0.  internal error: allow + log.
The reason never echoes a matched secret.
"""
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
    segs, cur, q, i = [], [], None, 0
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
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    segs.append("".join(cur))
    return segs


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
ALIAS_DEFINER = re.compile(r"(?<![\w.-])(?:alias|BASH_ALIASES|eval|source|expand_aliases|BASH_ENV)(?![\w-])|(?:^|[\s;&|(])\.\s+\S|\.(?:bashrc|bash_profile|bash_login|profile|zshrc|zshenv)(?![\w-])")  # a way to put text into the shell's alias table or its input
# ALIAS_DEFINER is also searched with quotes and backslashes removed (al""ias), and with _echo_shadowed (set -o posix, shopt, builtin, command);
# a command word that starts with a variable ($a$b x=...) can build the word alias at run time. The dot-source alternative also matches
# find . -name x: that only widens the quote-blind pass, which refuses any piece that names or globs into the token folder.
VARIABLE_COMMAND_WORD = re.compile(r"(?:^|[;&|\n(){!]|(?<![\w.-])(?:then|do|else|elif|time|if|while|until|coproc|in))[ \t]*"
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
        w = None if len(w) > 2000 or w.count("[") > 64 else _bracket_translate(w)  # a word this long is not a path to read: refuse, and keep the hook inside its time limit
        if w is None:
            yield None
            return
        for m in glob.iglob(w):
            yield m


def _bracket_translate(w):
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
        while j < n and w[j] != "]":
            if w[j] == "[" and j + 1 < n and w[j + 1] in "=.":
                return None
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
        if j >= n:  # no closing ]: the [ is an ordinary letter
            out.append("[")
            i += 1
            continue
        out.append("?" if wide else "[" + ("!" if neg else "") + "".join(body) + "]")
        i = j + 1
    return "".join(out)


def gmail_resolves_into_folder(seg, tolerant=False):
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
    cd = bool(words) and os.path.basename(words[0]) in ("cd", "pushd")

    def inside(path):  # the folder, below it, or one of its parents (a find, grep -r, cp -r or tar of a parent walks into it)
        real = os.path.realpath(os.path.expanduser(gmail_norm(path)))
        return real == folder or real in parents or real.startswith(folder + os.sep)

    for i, w in enumerate(words):
        if not w:
            continue
        if cd and i > 0 and not w.startswith("-"):
            if inside(w):
                return True
            continue
        # an option's value (--file=PATH, -fPATH is not seen) and an @FILE word are paths too
        after_eq = w.split("=", 1)[1] if "=" in w else ""
        # a redirect glued to its path (<./lnk/token.json, 0<path, 2>path, x>path) is that path: check every piece cut at the redirect operators
        # (and a path glued to a closing parenthesis or a backtick, as in $(cat ~/.?onfig/a?a/g?ail/token.json), is that path)
        for cand in {p.lstrip("@") for c in (w, after_eq) for p in [c] + re.split(r"[<>()`]+&?", c)}:
            if cand and ("/" in cand or cand.startswith(("~", "."))):
                if inside(cand):
                    return True
                if GMAIL_GLOB.search(cand):
                    n = 0
                    for m in bash_glob(os.path.expanduser(gmail_norm(cand))):
                        n += 1
                        if m is None or n > 500 or inside(m):  # too many matches to check in the hook's time, or a form not understood: refuse rather than guess
                            return True
    return False


# Claude_Commandz issue 228: the body of a quoted heredoc (<<'EOF', <<"EOF", <<\EOF) is not read by the shell, so it is data, not commands, when the
# command line it feeds only stores or posts text. Word-splitting it as shell failed on any lone apostrophe (and the guard refuses what it can't
# split), and a send path in a sentence was read as a send. Such a body is taken out of the text that is word-split and checked for a send address;
# it is still searched for the token folder's name. The command it feeds stays checked in full whenever ANY command on its line is not on this short
# list (an interpreter, a shell, curl, xargs, eval, a path-qualified name, a variable assignment in front), and whenever anything is unclear.
HEREDOC_DATA_WORDS = {"cat", "tee", "wc", "uniq", "head", "tail", "tr", "cut"}  # not sort: --compress-program=sh pipes its input through a shell
HEREDOC_DATA_GH = {"issue", "pr", "release", "gist", "discussion"}
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
            return cmd, [], [], cmd
        if c in "'\"":
            q = c
        elif c == "#" and (not line or (line[-1][-1:] in " \t;&|()\n" and not line[-1].startswith("\\")
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
            data_line = _heredoc_line_is_data(text)
            no_shell = _line_runs_no_shell(text)
            emit(text)
            for word, tabs, quoted, _op in pending:
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
                if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm) or gmail_resolves_into_folder(seg, True):
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
    code, bodies, opaque, _send = split_data_heredocs(str(body))
    for b in bodies:
        norm = gmail_norm(b)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm):
            return "folder"
    for seg in split_segments(code):
        norm = gmail_norm(seg)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm) or gmail_resolves_into_folder(seg):
            return "folder"
        if names_send(norm):
            return "send"
    return _opaque_use(opaque)


def gmail_credential_use(cmd):
    """Return "folder" or "send" when cmd would touch the mail token's folder or names a Gmail send address; else None."""
    whole = gmail_norm(cmd)
    if re.search(r"gmail_api\.py|redact_view\.py", whole) and GMAIL_ENV_INJECT.search(whole):
        return "folder"  # the two scripts that may hold the token must not run with variables that load other code
    cmd, bodies, opaque, send_cmd = split_data_heredocs(cmd)
    for body in bodies:  # a data body is not split into words, but one that names the token folder is still refused
        norm = gmail_norm(body)
        if GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm):
            return "folder"
    for seg in split_segments(cmd):
        norm = gmail_norm(seg)
        if (GMAIL_DIR.search(norm) or GMAIL_AFA_GLOB.search(norm) or GMAIL_CD_CONFIG.search(norm)
                or gmail_resolves_into_folder(seg)) and not gmail_segment_ok(seg):
            return "folder"
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
                    or gmail_resolves_into_folder(seg, tolerant=True)):
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


def prints_sensitive(cmd):
    all_segs = split_segments(cmd)
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
    for seg in segs:
        label = sensitive_in(seg)
        if not label:
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
        g = gmail_credential_use(cmd)
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
