#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block literal secrets and credential-file dumps.

stdin: {"tool_name": ..., "tool_input": {...}}
block: reason on stderr, exit 2.  allow: exit 0.  internal error: allow + log.
The reason never echoes a matched secret.
"""
import importlib.util
import json
import os
import re
import shlex
import stat
import sys
import time
import urllib.parse

WORD = (r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))"
        r"|credential|private[_-]?key|client[_-]?secret|session|cookie|bearer|webhook)")
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
NAME_NOT_SECRET = re.compile(r"(?:file|path|dir|url|uri|name|type|mode|host|port|header|endpoint"
                             r"|enabled|ttl|expiry|expires)$", re.I)
FLAG_SP = re.compile(r"(?<![\w-])--(?:password|passwd|token|secret|api-?key|auth-token|access-token"
                     r"|client-secret|private-key)\s+(?P<val>" + VAL + ")", re.I)
# A URL query parameter (the same unspaced word holds ://, so a shell & does not count) whose value is a plain 7 to 10 digit number is an id, not a secret (Greenhouse's public job id in
# job-boards.greenhouse.io/embed/job_app?for=x&token=8257322). The digits must end at a non-word character, so a value that goes on into
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
           "grep rg diff").split()
VERB = re.compile(r"(?<![\w.-])(" + "|".join(READERS) + r")(?![\w.-])")
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


def find_secret(cmd):
    for label, rx in TOKENS:
        if re.search(rx, cmd):
            return label
    for m in ASSIGN.finditer(cmd):
        if m.start() and cmd[m.start() - 1] in "?&" and URL_ID.match(m.group("val")) and "://" in re.split(r"\s", cmd[:m.start()])[-1]:
            continue
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


def prints_sensitive(cmd):
    if "redact_view.py" in cmd:
        return None
    whole = sensitive_in(cmd)
    if not whole:
        return None
    if INTERP.search(blank_quotes(cmd)) and INTERP_EVAL.search(cmd):
        return whole
    for seg in split_segments(cmd):
        label = sensitive_in(seg)
        if not label:
            continue
        scan = seg if SHELL_C.search(seg) else blank_quotes(seg)
        verbs = set(VERB.findall(scan))
        if SHELL_C.search(seg) and verbs:
            return label
        if verbs - {"grep", "rg"}:
            return label
        if verbs and not GREP_QUIET.search(scan):
            return label
    return None


# Rule F: the account email never goes to a website. Claude Code can put it in a User-Agent
# header when a site asks scripted visitors for a contact (anthropics/claude-code issue 107757).
# The address is read from the Claude config at run time, never written here, and never echoed.
# Refused: a Bash or WebFetch call that holds the address with a User-Agent header, in the URL
# (path or query), or in a request body (curl -d/--data/-F/--json, wget --post-data, python
# requests/httpx/urllib, node fetch/axios). Allowed: git, gh and our own gmail_api.py (they talk
# to GitHub and Gmail, which already know the address); the Gmail connector is not a Bash call.
# The exemptions fail closed: a git or gh call is plain only if, after shell quote removal, every URL, scp
# remote and --repo host is GitHub and nothing sets config (-c), a proxy, GIT_*/GH_* or a substitution.
# A remote given by name (git fetch origin) is trusted, as its URL lives in the repo's own config.
# Limit: it sees the address only when it is written in the call, not when a script reads it itself.
# The hook's matcher in settings.json must list WebFetch for the WebFetch half to run.
F_CONFIG = os.environ.get("SECRET_GUARD_CLAUDE_CONFIG")
_account_email = False
UA_HEADER = re.compile(r"user[-_ ]?agent", re.I)
F_HTTP_PROGS = {"curl", "wget", "http", "https", "xh", "httpie", "nc", "ncat", "socat", "lwp-request"}
F_HTTP_WORD = re.compile(r"(?<![\w./-])(?:curl|wget|xh|lwp-request|Invoke-WebRequest|Invoke-RestMethod)(?![\w-])", re.I)
F_UA_FLAG = re.compile(r"(?<!\S)(?:-[AU]|--user-?agent)(?![\w-])")
F_BODY_FLAG = re.compile(r"(?<!\S)(?:-d|-F|-T|--data[\w-]*|--form[\w-]*|--json|--post-data|--post-file"
                         r"|--body-data|--body-file|--upload-file)(?![\w-])|(?<!\S)-d\S|--(?:data|post-data|json)[\w-]*=")
F_HTTP_LIB = re.compile(r"\b(?:requests\.(?:get|post|put|patch|delete|request|Session)|httpx|urllib|aiohttp"
                        r"|http\.client|urlopen|pycurl|fetch\s*\(|axios|node-fetch|got\s*\(|XMLHttpRequest"
                        r"|Net::HTTP|open-uri|LWP|WebClient|HttpClient)", re.I)
F_SCRIPT_RUNNER = re.compile(r"(?<![\w.-])(?:python[\d.]*|node|deno|bun|ruby|perl|php)(?![\w.-])")
F_REASON = ("secret-guard: rule F, this call would send the account email address to a website (%s). "
            "Sites may ask scripted visitors for a contact; use a generic client name instead "
            "(a User-Agent of \"claude-code\", say) or ask Jonathan. git, gh and gmail_api.py are exempt.")


def account_email():
    """The account email (lower case) from the Claude config, or None. Read on first use."""
    global _account_email
    if _account_email is not False:
        return _account_email
    _account_email = None
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    for path in ([F_CONFIG] if F_CONFIG else [cfg_dir and os.path.join(cfg_dir, ".claude.json"),
                                              os.path.expanduser("~/.claude.json")]):
        if not path or not os.path.exists(path):
            continue
        try:
            em = (json.load(open(path)).get("oauthAccount") or {}).get("emailAddress")
        except (OSError, ValueError, AttributeError) as e:
            log_error("secret-guard rule F could not read the Claude config: %s" % type(e).__name__)
            continue
        if isinstance(em, str) and "@" in em:
            _account_email = em.strip().lower()
            break
    return _account_email


def _forms(email):
    return (email, email.replace("@", "%40"))


def _has(text, email):
    low = text.lower()
    return any(f in low for f in _forms(email))


F_TRUSTED_GMAIL = os.path.realpath(os.path.expanduser("~/.claude/scripts/gmail_api.py"))
F_GITHUB_HOSTS = ("github.com", "githubusercontent.com")
F_SUBST = re.compile(r"\$\(|`|<\(|>\(")
F_SCHEME_URL = re.compile(r"(?:https?|ssh|git|ftps?|rsync)://[^\s'\"]*", re.I)
F_SCP_REMOTE = re.compile(r"(?<![\w.%+-])(?:[\w.-]+@)?([\w.-]+\.[a-z]{2,}):(?!//)[\w~./-]", re.I)
F_PY_FLAGS = {"-u", "-B", "-I", "-E", "-s", "-S"}
F_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A proxy, GIT_*/GH_*/SSH_* or interpreter setting anywhere in a command that holds the address is refused.
F_ENV_NAME = re.compile(r"^(?:[A-Za-z0-9_]*proxy|GIT_\w+|GH_\w+|SSH_\w+|PYTHON\w*|NODE_\w+|HOME|XDG_\w+|PATH|LD_\w+)$", re.I)
F_ENV_RISKY = re.compile(r"(?<![\w-])(?:[A-Za-z0-9_]*proxy|GIT_\w+|GH_\w+|SSH_\w+|PYTHON\w*|NODE_\w+|HOME|XDG_\w+|LD_\w+)=",
                         re.I)


def _f_env_risky(cmd):
    """A risky variable set anywhere in the command, seen in the text and again after shell quote removal."""
    if F_ENV_RISKY.search(cmd):
        return True
    for seg in split_segments(cmd):
        toks = _f_tokens(seg)
        if toks is None:
            return True
        if any("=" in t and F_ENV_NAME.match(t.split("=", 1)[0]) for t in toks):
            return True
    return False
# git or gh options that change where it talks, what it sends or what it runs.
F_GIT_OPTS = re.compile(r"^(?:-c.*|--config-env.*|--exec-path.*|--upload-pack.*|--receive-pack.*|--exec|--hostname.*)$")
F_GIT_TEXT = re.compile(r"insteadof|sshcommand|gitproxy|ext::|core\.(?:pager|editor|fsmonitor|hookspath|askpass)"
                        r"|(?<![\w/])(?:http|credential|alias|diff|url)\.", re.I)


F_GIT_SUB = {"add", "commit", "log", "status", "diff", "show", "fetch", "pull", "push", "clone", "checkout",
             "switch", "branch", "merge", "tag", "remote", "stash", "worktree", "rev-parse", "rev-list",
             "ls-files", "ls-remote", "blame", "reset", "restore", "cherry-pick", "describe", "shortlog",
             "mv", "rm", "init", "config"}
F_GH_SUB = {"api", "issue", "pr", "repo", "run", "release", "workflow", "search", "gist", "label", "browse"}
F_GIT_CONFIG_OK = {"user.email", "user.name", "--get", "--get-all", "--list", "-l"}


def _f_subcommand(prog, args):
    """First subcommand word of a git or gh call, skipping -C <dir> and -R <repo> style options."""
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ("-C", "-R", "--repo", "--git-dir", "--work-tree", "--namespace"):
            skip = True
        elif not a.startswith("-"):
            return a
    return ""


def _host_is_github(host):
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in F_GITHUB_HOSTS)


def _f_tokens(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return None


def _f_plain_github_call(segment, email):
    """True only for a git or gh call that is plainly GitHub's: quotes removed first, every URL,
    scp-style remote and -R/--repo value on a GitHub host, and no config, proxy or env setting."""
    toks = _f_tokens(segment)
    if toks is None or F_SUBST.search(blank_quotes(segment)) or F_HTTP_WORD.search(blank_quotes(segment)):
        return False
    tokens, i = _process_command(segment)
    if any(F_ENV_ASSIGN.match(t) for t in tokens[:i]):
        return False                      # an env assignment before the program
    if i >= len(tokens) or os.path.basename(tokens[i]) not in ("git", "gh"):
        return False
    args = tokens[i + 1:]
    prog = os.path.basename(tokens[i])
    sub = _f_subcommand(prog, args)
    if sub not in (F_GIT_SUB if prog == "git" else F_GH_SUB):
        return False                      # an unknown subcommand, a user alias or a gh extension
    if sub == "config":
        keys = [a for a in args[args.index("config") + 1:] if not a.startswith("-") or a in F_GIT_CONFIG_OK]
        if not keys or any(k not in F_GIT_CONFIG_OK and k != args[-1] for k in keys[:1]):
            return False
        if keys[0] not in F_GIT_CONFIG_OK:
            return False
    if any(F_GIT_OPTS.match(a) for a in args) or F_GIT_TEXT.search(" ".join(args)):
        return False
    for n, a in enumerate(args):
        if a in ("-R", "--repo") and n + 1 < len(args):
            repo = args[n + 1]
        elif a.startswith("--repo="):
            repo = a[7:]
        else:
            repo = None
        if repo is not None and repo.count("/") >= 2 and not _host_is_github(repo.split("/")[0]):
            return False
        for u in F_SCHEME_URL.findall(a):
            try:
                if not _host_is_github(urllib.parse.urlsplit(u).hostname):
                    return False
            except ValueError:
                return False
        bare = F_SCHEME_URL.sub(" ", a)
        for f in _forms(email):
            bare = re.sub(re.escape(f), " ", bare, flags=re.I)
        if any(not _host_is_github(m.group(1)) for m in F_SCP_REMOTE.finditer(bare)):
            return False
    return True


def _f_program(segment):
    """Program a segment runs; 'gmail_api.py' only for our installed copy run straight by an
    interpreter (no env assignment, no -c/-m, no substitution, no web client word)."""
    toks, i = _process_command(segment)
    if i >= len(toks):
        return ""
    prog = os.path.basename(toks[i])
    if F_SCRIPT_RUNNER.fullmatch(prog) and not F_SUBST.search(blank_quotes(segment)) \
            and not any(F_ENV_ASSIGN.match(t) for t in toks[:i]):
        j = i + 1
        while j < len(toks) and toks[j] in F_PY_FLAGS:
            j += 1
        if j < len(toks) and os.path.realpath(os.path.expanduser(toks[j])) == F_TRUSTED_GMAIL \
                and not F_HTTP_WORD.search(blank_quotes(segment)):
            return "gmail_api.py"
    return prog


def _f_runs_client(seg, prog):
    return prog in F_HTTP_PROGS or bool(F_HTTP_WORD.search(blank_quotes(seg)))


def email_leak(cmd, email):
    """Where in a Bash command the account email would go to a website, else None.
    One command that holds the address and runs a web client anywhere is refused, so the address
    cannot be carried over in a variable, a pipe or a file between segments. The git, gh and
    gmail_api.py exemptions fail closed: anything unusual about the call voids them."""
    cmd = cmd.replace("\\\n", " ")
    if not _has(cmd, email):
        return None
    segs = split_segments(cmd)
    progs = [_f_program(s) for s in segs]
    if _f_env_risky(cmd):
        return "a proxy or environment setting that changes how the call connects"
    for seg, prog in zip(segs, progs):
        if prog == "gmail_api.py":
            continue
        if prog in ("git", "gh") and _f_plain_github_call(seg, email):
            continue
        if prog in ("git", "gh") and not _f_runs_client(seg, prog):
            return "a git or gh call that is not plainly GitHub's"
        if _has(seg, email) and UA_HEADER.search(seg):
            return "a User-Agent header"
        if _f_runs_client(seg, prog):
            if F_UA_FLAG.search(seg):
                return "a User-Agent header"
            if any(_has(u, email) for u in re.findall(r"https?://[^\s'\"]*", seg)):
                return "the URL"
            if F_BODY_FLAG.search(seg):
                return "a request body"
            return "a web request in the same command"
    rest = "\n".join(s for s, pr in zip(segs, progs) if pr != "gmail_api.py")
    if F_SCRIPT_RUNNER.search(rest) and F_HTTP_LIB.search(rest) and _has(rest, email):
        return "a request made from a script"
    return None


def account_email_rule(tool, ti):
    """(rule, reason) when this call sends the account email to a website, else None."""
    if tool == "Bash":
        text = ti.get("command")
    elif tool == "WebFetch" or (isinstance(tool, str) and tool.endswith("browser_navigate")):
        text = ti.get("url")
    else:
        return None
    if not isinstance(text, str) or ("@" not in text and "%40" not in text.lower()):
        return None
    email = account_email()
    if not email:
        return None
    where = email_leak(text, email) if tool == "Bash" else ("the URL" if _has(text, email) else None)
    return ("F", F_REASON % where) if where else None


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
        f = account_email_rule(tool, ti)
        if f:
            return f
        p = prints_sensitive(cmd)
        if p:
            return "B", ("secret-guard: this would print a credential-bearing file (%s). %s" % (p, HINT))
        for f in reader_targets(cmd, payload.get("cwd")):
            where = secret_in_file(f)
            if where:
                return "E", RULE_E % (f, where)
    elif tool == "WebFetch" or (isinstance(tool, str) and tool.endswith("browser_navigate")):
        return account_email_rule(tool, ti)
    elif tool == "Read" and isinstance(ti.get("file_path"), str):
        p = sensitive_in(ti["file_path"])
        if p:
            return "C", ("secret-guard: Read of a credential-bearing file (%s) is blocked. %s" % (p, HINT))
        where = secret_in_file(ti["file_path"])
        if where:
            return "E", RULE_E % (ti["file_path"], where)
    elif tool == "Grep":
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
        res = evaluate(json.loads(sys.stdin.read()))
    except Exception as e:
        log_error("secret-guard internal error: %s: %s" % (type(e).__name__, e))
        return 0
    if res:
        print(res[1], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
