#!/usr/bin/env python3
"""Print a config file with secrets masked.

usage: redact_view.py FILE [--path dotted.path|a::b::c] [--keys-only]

Tokens keep a 4-char prefix (gho_...[40]); every other secret becomes
<redacted:N> (N = length). Nothing of a non-token secret is ever printed.
"""
import argparse
import json
import re
import sys

WORD = (r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))"
        r"|credential|private[_-]?key|client[_-]?secret|session|cookie|bearer|webhook)")
KEY_RE = re.compile(WORD, re.I)
SAFE_KEY = re.compile(r"(?:(?:max|min)[_-]?\w*tokens|tokens?[_-](?:limit|count|used))$", re.I)

PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
TOKENS = re.compile("|".join([
    r"gh[opsu]_\w{20,}", r"github_pat_\w{20,}", r"sk-[A-Za-z0-9_-]{20,}",
    r"sk_(?:live|test)_\w{10,}", r"AKIA[0-9A-Z]{16}", r"ASIA[0-9A-Z]{16}",
    r"AIza[\w-]{30,}", r"xox[abprs]-[\w-]{10,}", r"glpat-[\w-]{20,}",
    r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}",
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
]
VAL = r"""(?:"[^"]*"|'[^']*'|[^\s"';&|,\]})]+)"""
ASSIGN = re.compile(r"(?P<name>[A-Za-z0-9_.-]*" + WORD + r"[A-Za-z0-9_.-]*)=(?P<val>" + VAL + ")", re.I)
FLAGWORD = r"--?(?:password|passwd|pass|pwd|token|api-?key|secret|auth-token|access-token|client-secret)"
FLAG_SP = re.compile(r"(?<![\w-])(?P<flag>" + FLAGWORD + r"|-p)(?P<sp>\s+)(?P<val>" + VAL + ")", re.I)
FLAG_ONLY = re.compile(r"^(?:" + FLAGWORD + r"|-p)$", re.I)
MASKED = re.compile(r"^(?:<redacted:\d+>|\w{3,4}…\[\d+\])$")
NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


def red(n):
    return "<redacted:%d>" % n


def unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
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

    return FLAG_SP.sub(f, ASSIGN.sub(a, s))


def secret_key(k):
    return bool(KEY_RE.search(k)) and not SAFE_KEY.search(k)


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
        elif secret_key(key) and val.strip() not in ("", "|", ">", "|-", ">-") and is_literal(val):
            out.append("%s%s%s%s" % (m.group("pre"), m.group("key"), m.group("sep"),
                                     red(len(unquote(val.split(" #")[0])))))
        else:
            out.append(m.group("pre") + scrub(m.group("key")) + m.group("sep") + scrub(val))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="View a file with secrets masked.")
    ap.add_argument("file")
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
    if data is not None or text.strip() in ("null",):
        if a.path:
            segs = a.path.split("::") if "::" in a.path else a.path.split(".")
            try:
                data = resolve(data, segs)
            except (KeyError, IndexError, ValueError):
                print("redact_view: path not found", file=sys.stderr)
                return 1
        if a.keys_only:
            shape(data)
        else:
            print(json.dumps(walk(data), indent=2, ensure_ascii=False))
    else:
        print(mask_text(text, a.keys_only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
