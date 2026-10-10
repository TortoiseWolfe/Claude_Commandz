#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block literal secrets and credential-file dumps.

stdin: {"tool_name": ..., "tool_input": {...}}
block: reason on stderr, exit 2.  allow: exit 0.  internal error: allow + log.
The reason never echoes a matched secret.
"""
import json
import os
import re
import sys
import time

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
    # A webhook's secret is a path segment, so no key name or token prefix gives it away
    # (2026-10-10). A long segment after /webhook(s)/ is the token; docs links stay allowed.
    ("webhook URL", r"https?://[^\s/'\"]+(?:/[^\s/'\"]+?)*?/webhooks?/[^\s'\"]*?[\w-]{24,}"),
    ("Slack webhook", r"https?://hooks\.slack\.com/(?:services|workflows|triggers)/[\w/]{20,}"),
]
VAL = r"""(?:"[^"]*"|'[^']*'|[^\s"';&|)]+)"""
ASSIGN = re.compile(r"(?P<name>[A-Za-z0-9_.-]*" + WORD + r"[A-Za-z0-9_.-]*)=(?P<val>" + VAL + ")", re.I)
NAME_NOT_SECRET = re.compile(r"(?:file|path|dir|url|uri|name|type|mode|host|port|header|endpoint"
                             r"|enabled|ttl|expiry|expires)$", re.I)
FLAG_SP = re.compile(r"(?<![\w-])--(?:password|passwd|token|secret|api-?key|auth-token|access-token"
                     r"|client-secret|private-key)\s+(?P<val>" + VAL + ")", re.I)
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


def evaluate(payload):
    """Return (rule, reason) to block, or None to allow."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input", {}), dict):
        raise ValueError("unexpected payload shape")
    tool, ti = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool == "Bash" and isinstance(ti.get("command"), str):
        cmd = ti["command"]
        s = find_secret(cmd)
        if s:
            return "A", ("secret-guard: rule A, this command contains a literal secret (%s). "
                         "Never put secrets inline in commands; read them from a file or env var "
                         "inside a script, and reference them as $VAR." % s)
        p = prints_sensitive(cmd)
        if p:
            return "B", ("secret-guard: this would print a credential-bearing file (%s). %s" % (p, HINT))
    elif tool == "Read" and isinstance(ti.get("file_path"), str):
        p = sensitive_in(ti["file_path"])
        if p:
            return "C", ("secret-guard: Read of a credential-bearing file (%s) is blocked. %s" % (p, HINT))
    elif tool == "Grep":
        p = sensitive_in("%s %s" % (ti.get("path") or "", ti.get("glob") or ""))
        if p and ti.get("output_mode") == "content":
            return "D", ("secret-guard: Grep content output on a credential-bearing file (%s) is "
                         "blocked (use output_mode files_with_matches or count). %s" % (p, HINT))
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
