#!/usr/bin/env python3
"""Print a config file with secrets masked.

usage: redact_view.py FILE [--path dotted.path|a::b::c] [--keys-only] [--no-known-secrets]

Tokens keep a 4-char prefix (gho_...[40]); every other secret becomes
<redacted:N> (N = length). Nothing of a non-token secret is ever printed.

FILE may be /dev/stdin, for a CI log: `gh run view <id> --log | redact_view.py /dev/stdin`.
A value is masked when its name is secret-looking (password, pass, secret, token, key, private,
credential, dsn, database url), in any of these forms, indented or after a CI timestamp:
  NAME: value    NAME=value    export NAME=value    "NAME": "value"
It is also masked, whatever sits beside it, when it equals a value in this machine's env files or
secret folder (the same collection known_secrets_scan.py uses); it then prints as <redacted:KEY>.
--repos, --secrets-dir and --ignore change where those values come from; --no-known-secrets skips it.
"""
import argparse
import json
import os
import re
import sys

WORD = (r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))"
        r"|credential|private[_-]?key|client[_-]?secret|session|cookie|bearer|webhook)")
KEY_RE = re.compile(WORD, re.I)
# A field named just "key", or <kind>_key / <kind>Key, holds a secret too: OBS keeps the Twitch
# stream key in service.json under "settings.key". Names that merely contain "key" (keyboard,
# monkey, keys_only, hotkey_name) are not matched.
KEYNAME = re.compile(r"(?:^|[^a-z0-9])(?:stream|licen[cs]e|signing|encryption|master|access|secret)?[_-]?key$", re.I)
SAFE_KEY = re.compile(r"(?:(?:max|min)[_-]?\w*tokens|tokens?[_-](?:limit|count|used))$", re.I)

PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
TOKENS = re.compile("|".join([
    r"gh[opsu]_\w{20,}", r"github_pat_\w{20,}", r"sk-[A-Za-z0-9_-]{20,}",
    r"sk_(?:live|test)_\w{10,}", r"AKIA[0-9A-Z]{16}", r"ASIA[0-9A-Z]{16}",
    r"AIza[\w-]{30,}", r"xox[abprs]-[\w-]{10,}", r"glpat-[\w-]{20,}",
    r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}",
    r"live_\d{5,}_[A-Za-z0-9]{20,}",  # Twitch stream key
]))
# Credentials carried inside a URL, whatever its key is called. A webhook's token is a path
# segment, so neither the key name nor a token shape gives it away. The host and the path up to
# the secret stay visible, so you can still tell what the URL is for.
URL_SECRETS = [
    # scheme://user:password@host (database URLs, basic auth)
    re.compile(r"(?P<pre>\b[a-z][\w+.-]*://[^\s:/@'\"<>]*:)(?P<secret>[^\s@/'\"<>]+)(?=@)", re.I),
    # everything after /webhook/ or /webhooks/ (Discord, Teams and most others)
    re.compile(r"(?P<pre>\bhttps?://[^\s/'\"<>]+(?:/[^\s/'\"<>]+?)*?/webhooks?/)(?P<secret>[^\s'\"<>]+)", re.I),
    # hooks.<service>/<kind>/... (Slack, Zapier)
    re.compile(r"(?P<pre>\bhttps?://hooks\.[^\s/'\"<>]+/[^\s/'\"<>]+/)(?P<secret>[^\s'\"<>]+)", re.I),
    # RTMP stream URLs carry the stream key as their final path segment.
    re.compile(r"(?P<pre>\brtmps?://[^\s/'\"<>]+/[^/?#\s'\"<>]+/)(?P<secret>[^/?#\s'\"<>]+)", re.I),
]
VAL = r"""(?:"[^"]*"|'[^']*'|[^\s"';&|,\]})]+)"""
ASSIGN = re.compile(r"(?P<name>[A-Za-z0-9_.-]*" + WORD + r"[A-Za-z0-9_.-]*)=(?P<val>" + VAL + ")", re.I)
BARE_KEY_ASSIGN = re.compile(r"(?<![A-Za-z0-9_.-])(?P<name>(?:--)?(?:[A-Za-z0-9_.-]+[_-])?key)=(?P<val>" + VAL + ")", re.I)
FLAGWORD = r"--?(?:password|passwd|pass|pwd|token|api-?key|secret|auth-token|access-token|client-secret)"
FLAG_SP = re.compile(r"(?<![\w-])(?P<flag>" + FLAGWORD + r"|-p)(?P<sp>\s+)(?P<val>" + VAL + ")", re.I)
FLAG_ONLY = re.compile(r"^(?:" + FLAGWORD + r"|-p)$", re.I)
# "Password: value" inside a line of prose or a list ("Email: x / Password: `y`"), where the line as a whole
# is not a key: value pair for LINE below to catch.
PROSE_PASS = re.compile(r"(?P<pre>\b(?:pass(?:word|wd|code)?|pwd)\b\**\s*[:=]\s*\**\s*)"
                        r"(?P<val>`[^`\n]+`|\"[^\"\n]*\"|'[^'\n]*'|[^\s`\"',;|)]+)", re.I)
MASKED = re.compile(r"^(?:<redacted:\d+>|\w{3,4}…\[\d+\]|\*{3,})$")  # *** is GitHub Actions' own mask
NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")

# "NAME: value", "NAME=value", "export NAME=value" and JSON "NAME": "value" anywhere in a line, so a CI
# log line like `2026-11-07T20:30:00.1234567Z   TEST_USER_A_PASSWORD: value` is caught even though
# the timestamp, not the name, is what LINE sees as the key. A name is secret-looking when one of
# its words (split on _ - . and camelCase) is a secret word, or it is DATABASE_URL / a connection string.
SECRET_WORDS = {"pass", "password", "passwd", "pwd", "passphrase", "passcode", "secret", "secrets", "token",
                "tokens", "key", "apikey", "private", "credential", "credentials", "dsn"}
NAME_VALUE = re.compile(r"""(?<![A-Za-z0-9_.-])(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)(?P<q>["']?)"""
                        r"""(?P<sep>[ \t]*[:=][ \t]*)"""
                        # a quoted string (backslash escapes allowed), else the rest of the line: an
                        # unterminated quote or a value with spaces must not leave a tail showing
                        r"""(?P<val>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|["'][^\n]*|[^\s"'][^\n]*)""")


def secret_word_name(name, val=""):
    if SAFE_KEY.search(name):
        return False
    low = re.sub(r"[^a-z0-9]", "", name.lower())
    if low.endswith(("databaseurl", "connectionstring")):
        # A URL's own password is already masked by URL_SECRETS, which keeps the host readable; a
        # value that is not a URL is masked whole.
        return not re.match(r"[a-z][\w+.-]*://", unquote(val), re.I)
    words = re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", name)
    return any(w.lower() in SECRET_WORDS for w in words if w)


def red(n):
    return "<redacted:%d>" % n


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    # A markdown code span (`value`) holds a literal; docs write "- Password: `value`" this way.
    if len(v) >= 3 and v[0] == v[-1] == "`" and "`" not in v[1:-1]:
        return v[1:-1]
    return v


def is_literal(v):
    v = unquote(v)
    if not v or v[0] in "$`" or MASKED.match(v) or NUMERIC.match(v):
        return False
    return v.lower() not in ("true", "false", "null", "none", "yes", "no")


def scrub(s):
    """Mask token shapes and NAME=literal / --flag literal fragments in a string."""
    s = PEM.sub(lambda m: red(len(m.group(0))), s)
    s = TOKENS.sub(lambda m: "%s…[%d]" % (m.group(0)[:4], len(m.group(0))), s)
    for rx in URL_SECRETS:
        s = rx.sub(lambda m: m.group("pre") + red(len(m.group("secret"))), s)

    def a(m):
        if SAFE_KEY.search(m.group("name")) or not is_literal(m.group("val")):
            return m.group(0)
        return m.group("name") + "=" + red(len(unquote(m.group("val"))))

    def f(m):
        if not is_literal(m.group("val")):
            return m.group(0)
        return m.group("flag") + m.group("sp") + red(len(unquote(m.group("val"))))

    s = FLAG_SP.sub(f, ASSIGN.sub(a, s))

    def prose(m):
        if not is_literal(m.group("val")):
            return m.group(0)
        return m.group("pre") + red(len(unquote(m.group("val"))))

    s = PROSE_PASS.sub(prose, s)

    def name_value(m):
        val, tail = m.group("val"), ""
        head = m.group("name") + m.group("q") + m.group("sep")
        secret = secret_word_name(m.group("name"), val)
        complete_quote = len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]
        if re.search(r"\s", val) and not complete_quote and (not secret or ("=" in m.group("sep") and val[0] not in "\"'")):
            # Shell style (NAME=secret npm run e2e): the value ends at the first space, the command stays.
            # A name that is not a secret takes one word only, so a later pair on the same line
            # (`Error: x DB_PASSWORD: value`, `FOO=bar DSN=value cmd`) is still found in the tail.
            val, tail = re.split(r"(?=\s)", val, maxsplit=1)
        if not secret or not is_literal(val):
            return head + val + scrub_pairs(tail)
        quote = val[0] if val[0] in "\"'" and len(val) >= 2 and val[-1] == val[0] else ""
        return head + quote + red(len(unquote(val))) + quote + scrub_pairs(tail)

    def scrub_pairs(text):
        return NAME_VALUE.sub(name_value, text) if text else text

    s = scrub_pairs(s)

    def key_assignment(m):
        if not is_literal(m.group("val")):
            return m.group(0)
        return m.group("name") + "=" + red(len(unquote(m.group("val"))))

    return BARE_KEY_ASSIGN.sub(key_assignment, s)


def secret_key(k):
    return bool(KEY_RE.search(k) or KEYNAME.search(k)) and not SAFE_KEY.search(k)


def walk(o, key="", force=False):
    if isinstance(o, dict):
        return {scrub(str(k)): walk(v, str(k), force or secret_key(str(k))) for k, v in o.items()}
    if isinstance(o, list):
        out, prev = [], None
        for v in o:
            if isinstance(v, str) and isinstance(prev, str) and FLAG_ONLY.match(prev) and is_literal(v):
                out.append(red(len(v)))
            else:
                out.append(walk(v, key, force))
            prev = v
        return out
    if isinstance(o, str):
        if (force or secret_key(key)) and is_literal(o):
            return red(len(o))
        return scrub(o)
    return o


def resolve(o, segs):
    if not segs:
        return o
    if isinstance(o, list):
        return resolve(o[int(segs[0])], segs[1:])
    if not isinstance(o, dict):
        raise KeyError(segs[0])
    for i in range(len(segs), 0, -1):  # longest key first: keys may contain dots
        k = ".".join(segs[:i])
        if k in o:
            try:
                return resolve(o[k], segs[i:])
            except (KeyError, IndexError, ValueError):
                continue
    raise KeyError(segs[0])


def shape(o, ind=0, name=None):
    pad = "  " * ind
    lab = (scrub(str(name)) + ": ") if name is not None else ""
    if isinstance(o, dict):
        print("%s%sobject(%d keys)" % (pad, lab, len(o)))
        for k, v in o.items():
            shape(v, ind + 1, k)
    elif isinstance(o, list):
        print("%s%sarray(%d)" % (pad, lab, len(o)))
        for i, v in enumerate(o[:10]):
            shape(v, ind + 1, "[%d]" % i)
        if len(o) > 10:
            print("%s  ... %d more" % (pad, len(o) - 10))
    elif isinstance(o, str):
        print("%s%sstring(len=%d)" % (pad, lab, len(o)))
    elif isinstance(o, bool):
        print("%s%sboolean" % (pad, lab))
    elif o is None:
        print("%s%snull" % (pad, lab))
    else:
        print("%s%snumber" % (pad, lab))


LINE = re.compile(r"""^(?P<pre>\s*(?:-\s+)?(?:export\s+)?)(?P<key>"?[^\s=:#"\[][^=:]*?"?)"""
                  r"""(?P<sep>\s*[=:]\s*)(?P<val>.*)$""")


def mask_text(text, keys_only=False):
    text = PEM.sub(lambda m: red(len(m.group(0))), text)
    out = []
    for line in text.split("\n"):
        m = LINE.match(line)
        if not m:
            out.append("<line len %d>" % len(line) if keys_only and line.strip() else
                       ("" if keys_only else scrub(line)))
            continue
        key, val = m.group("key").strip('"'), m.group("val")
        if keys_only:
            out.append("%s%s%s<value len %d>" % (m.group("pre"), scrub(key), m.group("sep"), len(val)))
        elif (secret_key(key) or secret_word_name(key, val)) and val.strip() not in ("", "|", ">", "|-", ">-") and is_literal(val):
            out.append("%s%s%s%s" % (m.group("pre"), m.group("key"), m.group("sep"),
                                     red(len(unquote(val.split(" #")[0])))))
        else:
            out.append(m.group("pre") + scrub(m.group("key")) + m.group("sep") + scrub(val))
    return "\n".join(out)


MIN_KNOWN = 4  # shorter values would garble ordinary words; the name-based masking still covers them


def known_needles(repos, secrets_dir, ignore):
    """[(text form, KEY)] for every value in this machine's env files and secret folder, longest first.
    Values stay in memory; nothing here prints one."""
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import known_secrets_scan as ks
    secrets = ks.collect(repos, ks.load_ignore(ignore), secrets_dir=secrets_dir)
    out = {}
    for value, (rel, key) in secrets.items():
        for form in ks.forms(value):
            try:
                text = form.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(text) >= MIN_KNOWN:
                out.setdefault(text, key)
    return sorted(out.items(), key=lambda kv: -len(kv[0]))


def mask_known(text, needles):
    """Find every occurrence of every form first, merge the ones that overlap, then replace each merged
    span once, so two forms that share characters cannot leave the end of one showing."""
    spans = []
    for needle, key in needles:
        i = text.find(needle)
        while i != -1:
            spans.append((i, i + len(needle), key))
            i = text.find(needle, i + 1)
    if not spans:
        return text
    spans.sort(key=lambda s: (s[0], -s[1]))
    merged = [list(spans[0])]
    for start, end, key in spans[1:]:
        if start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, key])
    out, pos = [], 0
    for start, end, key in merged:
        out.append(text[pos:start])
        out.append("<redacted:%s>" % key)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="View a file with secrets masked.")
    ap.add_argument("file")
    ap.add_argument("--repos", default=os.environ.get("REDACT_VIEW_REPOS") or os.path.expanduser("~/repos"),
                    help="folder whose env files supply the known secret values (default ~/repos)")
    ap.add_argument("--secrets-dir", default=os.environ.get("KNOWN_SECRETS_DIR") or
                    os.path.expanduser("~/.config/afa/secrets"))
    ap.add_argument("--ignore", default=os.environ.get("REDACT_VIEW_IGNORE") or
                    os.path.expanduser("~/.config/known-secrets/ignore.txt"))
    ap.add_argument("--no-known-secrets", action="store_true",
                    help="skip the pass that masks this machine's env-file values wherever they appear")
    ap.add_argument("--path", help="JSON subtree: dotted, or segments joined by '::'")
    ap.add_argument("--keys-only", action="store_true", help="structure only, no values")
    a = ap.parse_args(argv)
    try:
        with open(a.file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        print("redact_view: cannot read file (%s)" % e.strerror, file=sys.stderr)
        return 1
    try:
        data = json.loads(text)
    except ValueError:
        data = None
        if a.path:
            print("redact_view: --path needs a JSON file", file=sys.stderr)
            return 1
    needles = []
    if not a.no_known_secrets:
        try:
            needles = known_needles(a.repos, a.secrets_dir, a.ignore)
        except Exception as e:  # a viewer that cannot load the list still masks by name and shape
            print("redact_view: known-value list not loaded (%s); masking by name and shape only" % type(e).__name__,
                  file=sys.stderr)
    if data is not None or text.strip() in ("null",):
        if a.path:
            segs = a.path.split("::") if "::" in a.path else a.path.split(".")
            try:
                data = resolve(data, segs)
            except (KeyError, IndexError, ValueError):
                print("redact_view: path not found", file=sys.stderr)
                return 1
        if a.keys_only:
            # shape() prints as it goes; capture it so the known-value pass sees the same text
            import contextlib
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                shape(data)
            print(mask_known(buf.getvalue(), needles), end="")
        else:
            print(mask_known(json.dumps(walk(data), indent=2, ensure_ascii=False), needles))
    else:
        print(mask_known(mask_text(text, a.keys_only), needles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
