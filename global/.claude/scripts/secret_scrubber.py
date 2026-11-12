#!/usr/bin/env python3
"""Mask secret values in text a tool printed, before the model reads it (library, plus a stdin filter with --filter).

Use when: a PostToolUse hook, or a helper script's output, must never show a token, key, password or known secret value.

Library:
    scrub_text(text, tier=2, needles=None) -> (new_text, count)
    scrub_obj(obj, tier=2, needles=None)   -> (new_obj, count)   walks dicts/lists, scrubs every string, keeps the shape
    known_needles()                        -> [(text form, "known-secret")] of this machine's real values (cached)
Command line:
    some-command | python3 secret_scrubber.py --filter [--tier 1|2]
    reads stdin, writes stdout line by line (flushed), keeps state so a private key block is masked as a whole.
    A crash prints a withheld notice for the rest of the stream, never raw text.

Tier 1 (every tool): known secret values, token shapes (GitHub, Slack, AWS, Stripe, Supabase sbp_/sb_secret_, JWT ...),
    private key blocks, the password in scheme://user:password@host, Authorization / api-key header values (not when the
    value is code: an identifier, a dotted path like req.headers.authorization, an UPPER_CASE environment name).
Tier 2 (Bash, Grep, MCP, WebFetch, WebSearch; not Read/Edit/Write/NotebookEdit): the value of a field whose name says
    password / secret / token / api key, in JSON, KEY=value, key: value text and --flag=value. The name stays, the
    value goes. Code is left alone: token = get_token(), token = await getToken(), token = cfg.token or env_token,
    and type annotations (secret: str | None = None; api_key: str = "x" masks only the "x").
    A value that is one plain word (letters and underscores, 30 or fewer, no digit) is code too: privateKey: CryptoKey;
    password: string): colorToken: 'primary'. Still masked: --password=hunter, PGPASSWORD=hunter, a line-start
    password: hunter (no ; or , after it), and "password": "hunter" (a password-named field masks a quoted word).
    Also left alone: a value of only brackets or quotes (), {}, JSX onToken={onToken}, data?.session?.access_token.
Never prints, logs or returns a secret value except inside the text it was asked to scrub (which it masks).

Environment (tests): KNOWN_SECRETS_REPOS, KNOWN_SECRETS_DIR, KNOWN_SECRETS_IGNORE, KNOWN_SECRETS_CACHE (same names
secret-guard.py reads), SECRET_SCRUBBER_FAIL=1 (crash on every call), SECRET_SCRUBBER_FAIL_AFTER=N (crash after N calls).
"""
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
WITHHELD = "[withheld: the secret scrubber failed on this text]"
MIN_KNOWN = 4
KS_TTL = 3600


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_rv = _load("redact_view")

# ---------------------------------------------------------------------------------------------- known values
_needles = None


def _ks_paths():
    home = os.path.expanduser("~")
    return (os.environ.get("KNOWN_SECRETS_REPOS") or os.path.join(home, "repos"),
            os.environ.get("KNOWN_SECRETS_DIR") or os.path.join(home, ".config", "afa", "secrets"),
            os.environ.get("KNOWN_SECRETS_IGNORE") or os.path.join(home, ".config", "known-secrets", "ignore.txt"),
            os.environ.get("KNOWN_SECRETS_CACHE") or os.path.join(home, ".claude", "state", "known-secrets-envfiles.json"))


def known_needles():
    """[(text form, 'known-secret')], longest first. The env-file LIST is cached for an hour exactly as
    secret-guard.py does (path-only cache, same file); the values are read fresh each process."""
    global _needles
    if _needles is not None:
        return _needles
    src = os.path.join(HERE, "known_secrets_scan.py")
    if not os.path.exists(src):
        _needles = []
        return _needles
    ks = _load("known_secrets_scan")
    root, secrets_dir, ignore, cache = _ks_paths()
    files = None
    try:
        with open(cache) as fh:
            c = json.load(fh)
        if c.get("root") == root and time.time() - c.get("generated", 0) < KS_TTL:
            files = c["files"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    if files is None:
        files = list(ks.env_files(root))
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            fd = os.open(cache + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"root": root, "generated": time.time(), "files": files}, fh)
            os.replace(cache + ".tmp", cache)
        except OSError:
            pass
    found = ks.collect(root, ks.load_ignore(ignore), files=files, secrets_dir=secrets_dir)
    seen = set()
    for value in found:
        for form in ks.forms(value):
            try:
                text = form.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if len(text) >= MIN_KNOWN:
                seen.add(text)
    _needles = [(t, "known-secret") for t in sorted(seen, key=lambda t: -len(t))]
    return _needles


# ---------------------------------------------------------------------------------------------- tier 1 rules
SHAPES = re.compile(_rv.TOKENS.pattern + "|" + "|".join([
    r"sbp_[A-Za-z0-9]{20,}", r"sb_secret_[A-Za-z0-9_-]{10,}",
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
]))
PEM = _rv.PEM
PEM_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
PEM_BODY = re.compile(r"[A-Za-z0-9+/=]{16,}")     # a key-body line, once stripped
URL_PW = _rv.URL_SECRETS[0]   # only user:password@; the webhook patterns would mask ordinary API URLs
B64_RUN = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
AUTH_HDR = re.compile(r"""(?P<pre>(?<![\w-])(?:proxy-)?authorization(?:\\?["'])?[ \t]*[:=][ \t]*(?:\\?["'])?
                          (?:(?:bearer|basic|token|digest)[ \t]+)?)(?P<val>[A-Za-z0-9._~+/=-]{8,})""", re.I | re.X)
KEY_HDR = re.compile(r"""(?P<pre>(?<![\w-])(?:x-api-key|api-key|apikey|x-auth-token|x-access-token|x-goog-api-key)
                         (?:\\?["'])?[ \t]*[:=][ \t]*(?:\\?["'])?)(?P<val>[A-Za-z0-9._~+/=-]{8,})""", re.I | re.X)


def _mask(label):
    return "<redacted:%s>" % label


# A header value that is code, not a credential: req.headers.authorization, supabaseAnonKey, import.meta.env.VITE_API_KEY.
HDR_PATH = re.compile(r"^[A-Za-z_][A-Za-z_]*(?:\.[A-Za-z_][A-Za-z_]*)*$")     # identifiers / dotted path, no digits
HDR_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")                    # VITE_API_KEY
HDR_FIELD_NAME = re.compile(r"^(?:(?:x-)?api-?key|x-auth-token|x-access-token|x-goog-api-key|(?:proxy-)?authorization)$", re.I)
HDR_SCHEME = re.compile(r"(?:bearer|basic|token|digest)[ \t]+$", re.I)


def _code_header_value(v, pre):
    """True when the value after a header name is code. After a scheme word (Bearer, Basic ...) it is never skipped:
    a base64 Basic credential can be letters only."""
    if HDR_SCHEME.search(pre):
        return False
    if HDR_ENV_NAME.match(v):
        return True
    return bool(HDR_PATH.match(v) and len(v) <= (60 if "." in v else 40))


def _shape_label(tok):
    if tok.startswith("eyJ"):
        return "jwt"
    if tok.startswith(("sbp_", "sb_secret_")):
        return "supabase-key"
    return "token"


def _tier1(text, needles):
    n = 0
    if needles:
        new = _rv.mask_known(text, needles)
        if new != text:
            n += new.count(_mask("known-secret")) - text.count(_mask("known-secret"))
            text = new
    text, k = PEM.subn(_mask("private-key"), text)
    n += k
    # A block cut short (head -5 / tail -5 of a key file): complete blocks are gone by now, so a BEGIN still here has no
    # END after it, and an END still here has no BEGIN before it.
    m = PEM_BEGIN.search(text)
    if m:
        text = text[:m.start()] + _mask("private-key")
        n += 1
    while True:
        m = PEM_END.search(text)
        if not m:
            break
        start = text.rfind("\n", 0, m.start()) + 1
        while start > 0:      # the unbroken run of key-body lines right above the END line
            prev = text.rfind("\n", 0, start - 1) + 1
            if not PEM_BODY.fullmatch(text[prev:start - 1].strip()):
                break
            start = prev
        text = text[:start] + _mask("private-key") + text[m.end():]
        n += 1
    runs = [(m.start(), m.end()) for m in B64_RUN.finditer(text)]

    def shape(m):
        nonlocal n
        if any(a <= m.start() and m.end() <= b for a, b in runs):
            return m.group(0)
        n += 1
        return _mask(_shape_label(m.group(0)))
    if "eyJ" in text or SHAPES.search(text):
        text = SHAPES.sub(shape, text)

    def url(m):
        nonlocal n
        pw = m.group("secret")
        if pw.startswith(("$", "<", "{")) or "*" in pw or "redacted" in pw:
            return m.group(0)
        n += 1
        return m.group("pre") + _mask("url-password")
    text = URL_PW.sub(url, text)

    def header(m):
        nonlocal n
        v = m.group("val")
        if v.startswith(("$", "<")) or "redacted" in v:
            return m.group(0)
        if _code_header_value(v, m.group("pre")):
            return m.group(0)
        n += 1
        return m.group("pre") + _mask("auth-header")
    text = AUTH_HDR.sub(header, text)
    text = KEY_HDR.sub(header, text)
    return text, n


# ---------------------------------------------------------------------------------------------- tier 2 rules
# A field name is secret when it contains one of these (after camelCase is split into snake_case).
SECRET_NAME_PARTS = ("password", "passwd", "pwd", "passphrase", "secret", "token", "apikey", "api_key", "api-key",
                     "private_key", "privatekey", "service_role_key", "servicerolekey", "user_pass", "userpass")
SECRET_KEY_COMPOUND = re.compile(r"(?:^|[_.-])(?:api|private|service[_-]?role|signing|access|encryption|master|stream|"
                                 r"auth|licen[cs]e|client|jwt|session|anon|secret)[_-]?key$")
# ... unless one of these WHOLE words is also in the name (next_page_token, token_type, secret_name, password_path).
NAME_SKIP_WORDS = {"page", "next", "prev", "previous", "cursor", "type", "count", "counts", "id", "ids", "url", "uri",
                   "name", "names", "path", "paths", "expires", "expire", "expiry", "expiration", "max", "min",
                   "limit", "ttl", "length", "len", "size", "file", "dir", "env", "ref", "label", "header",
                   "endpoint", "host", "enabled", "required", "usage", "used", "tokens", "tokenizer", "hint",
                   "policy", "strength", "format", "kind", "scope", "scopes", "version"}
# A value that contains one of these AND looks like an identifier ("token": "access_token", an upper-case environment
# name like SUPABASE_JWT_SECRET) is a label or reference, not a value. "super-fake-jwt-token-with-..." or "mytokenpass1"
# are not identifier-like (hyphens, digits), so they are masked.
VALUE_SECRET_WORDS = re.compile(r"password|passwd|pwd|passphrase|secret|token|api[_-]?key|credential", re.I)
IDENT_VALUE = re.compile(r"^[A-Za-z_][A-Za-z_.]*$")
ENV_NAME_VALUE = re.compile(r"^[A-Z][A-Z0-9_]*$")


EMPTY_BRACKETS = re.compile(r"""^[\s()\[\]{}<>"'`,;:]*$""")


def _is_word(v):
    """W: after trailing punctuation is removed, one word of letters and underscores (30 or fewer, no digit)."""
    return bool(WORD_VALUE.match(VALUE_TAIL.sub("", v.strip())))


def _password_family(name):
    return bool(_words(name)[1] & PASSWORD_WORDS)


def _label_like(v):
    return bool((len(v) <= 40 and IDENT_VALUE.match(v)) or ENV_NAME_VALUE.match(v))

STOPWORDS = {"true", "false", "null", "none", "nil", "undefined", "yes", "no", "required", "optional", "string",
             "hidden", "masked", "redacted", "unset", "empty", "default", "value", "example", "changeme"}
NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\??\.[A-Za-z_]\w*)+!?$")     # also data?.session?.access_token  process.env.X!
FILLER = re.compile(r"^[*xX•.\-_#]+$")
# Anywhere in a line. A bare value runs to the next whitespace or quote (so ; & | ( ) [ ] do not end it).
# A command-line flag (--password=VALUE, -x=VALUE) may start the name after line start, whitespace or a quote.
FIELD = re.compile(r"""(?:(?<![\w.$-])|(?P<dash>(?<![^\s"'])-{1,2}(?=[A-Za-z_])))
                       (?P<q>\\?["']?)(?P<name>[A-Za-z_][\w.-]{0,80})(?P=q)(?P<sep>[ \t]*[:=][ \t]*)
                       (?![=~>])(?P<val>\\"(?:(?!\\").)*\\"|"(?:[^"\\\n]|\\.)*"|'[^'\n]*'|(?:[^\s"'\\]|\\(?!["']))+)""", re.X)
# A pair that starts the line (after spaces, export / declare -x / set / a YAML "- "): a bare value runs to end of line.
LINE_FIELD = re.compile(r"""^(?P<lead>[ \t]*(?:(?:-|export|declare[ \t]+-x|set)[ \t]+)*)(?P<q>\\?["']?)
                            (?P<name>[A-Za-z_][\w.-]{0,80})(?P=q)(?P<sep>[ \t]*[:=][ \t]*)
                            (?![=~>])(?P<val>[^\s"'\\][^\n\r]*)""", re.X | re.M)
JSX_EXPR = re.compile(r"^\{[A-Za-z_]+(?:\??\.[A-Za-z_]+)*\}[,;]?$")     # onToken={onToken}  colorToken={s.colorToken}: no digit
CODE_CALL = re.compile(r"^[\w.]+[(\[]")       # token = get_token()   password = os.environ["X"]: code, not a value
VALUE_TAIL = re.compile(r"[,;)}\]:>]+$")        # punctuation after a bare value ("password": null, string):) is not part of it
# W: one word of letters and underscores, 30 characters or fewer, no digit (a type name, a variable, a plain word).
# The one digit-bearing exception is a typed-array class (sharedSecret: Uint8Array,), a type name that cannot be a value.
WORD_VALUE = re.compile(r"^(?:[A-Za-z_]{1,30}|(?:Big)?(?:Uint|Int|Float)(?:8|16|32|64)(?:Clamped)?Array)$")
ENV_FIELD_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
PASSWORD_WORDS = {"password", "passwd", "pwd", "passphrase", "pass"}
# {"name": "JWT_SECRET", "value": "..."}  (also "key"/"value", either order); plain and backslash-escaped quotes.
PAIR_RES = []
for _q in ('"', '\\\\"'):
    _name = r'%s(?:name|key)%s\s*:\s*%s(?P<n>[^"\\\n]{1,80})%s' % (_q, _q, _q, _q)
    _value = r'%svalue%s\s*:\s*%s(?P<v>[^"\\\n]*)%s' % (_q, _q, _q, _q)
    PAIR_RES.append(re.compile(_name + r'\s*,\s*' + _value))
    PAIR_RES.append(re.compile(_value + r'\s*,\s*' + _name))
HAS_DIGIT = re.compile(r"\d")


def _words(name):
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    return snake, set(w for w in re.split(r"[^a-z0-9]+", snake) if w)


def secret_name(name):
    """(is_secret, bare_key) for a field name."""
    snake, words = _words(name)
    if snake == "key":
        return True, True
    if words & NAME_SKIP_WORDS:
        return False, False
    if any(p in snake for p in SECRET_NAME_PARTS) or SECRET_KEY_COMPOUND.search(snake):
        return True, False
    return False, False


def _random_looking(v):
    return len(v) >= 20 and not re.search(r"\s", v) and bool(HAS_DIGIT.search(v)) and bool(re.search(r"[A-Za-z]", v))


def literal_secret(value, bare_key=False):
    """True when value (quotes already removed) looks like a literal to mask, not a number, reference or label."""
    v = value.strip()
    if len(v) < 3 or NUMBER.match(v) or v.lower() in STOPWORDS or FILLER.match(v):
        return False
    if EMPTY_BRACKETS.match(v):      # (), {}, [], "", (),  : fewer than 3 letters or digits and only brackets / quotes
        return False
    if v.startswith(("$", "%", "<", "{{", "@")) or "redacted" in v.lower() or DOTTED.match(v):
        return False
    if VALUE_SECRET_WORDS.search(v) and _label_like(v):
        return False
    if bare_key and not _random_looking(v):
        return False
    return True


CODE_KEYWORD = re.compile(r"^(?:await|new|async|function|lambda|yield|typeof|not|return)\s")   # token = await getToken()
CODE_OPERATOR = re.compile(r"^[A-Za-z_][\w.]*\s+(?:or|and|if|as|in|is|\|\||&&|\?\?|\?|\+)(?:\s|$)")   # a or b
LEAD_ONLY = re.compile(r"^[ \t]*(?:(?:-|export|declare[ \t]+-x|set)[ \t]+)*$")     # what may precede a line-start pair
REST_OF_LINE = re.compile(r"[^\n\r]*")
# Type annotations:  name: str | None = None    name: Optional[str]    name: str = "value"
TYPE_ATOM = r"[A-Za-z_][\w.]*(?:\[[^\]\n]*\])?"
TYPE_UNION = TYPE_ATOM + r"(?:[ \t]*\|[ \t]*" + TYPE_ATOM + r")*"
ANNOT_ASSIGN = re.compile(r"^(?P<t>" + TYPE_UNION + r")[ \t]+=[ \t]+(?P<rest>\S.*)$")
ANNOT_TYPE = re.compile(r"^" + TYPE_UNION + r"[ \t]*[;,]?$")
QUOTED = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*'")
TYPE_WORDS = {"str", "int", "bool", "bytes", "float", "string", "number", "boolean", "any", "object", "unknown",
              "never", "void", "None", "Optional", "SecretStr"}
FIRST_WORD = re.compile(r"^(\w+)(?![\w.-])")


def _bare_is_secret(value, bare_key):
    """A bare (unquoted) value: code like get_token(), os.environ["X"], await getToken() or cfg.token or env is left
    alone; trailing , ; ) } ] are not judged as part of the value."""
    if CODE_CALL.match(value) or CODE_KEYWORD.match(value) or CODE_OPERATOR.match(value) or JSX_EXPR.match(value):
        return False
    return literal_secret(VALUE_TAIL.sub("", value), bare_key)


def _annotation(val, bare_key, label):
    """For a line-start 'name: <val>' with a colon: None when val is not a type annotation, else val with only the
    assigned literal masked (the type and its quotes stay). Equal to val when nothing needed masking."""
    m = ANNOT_ASSIGN.match(val)
    if m:
        rest, start = m.group("rest"), m.start("rest")
        q = QUOTED.match(rest)
        if q:
            if not literal_secret(q.group(0)[1:-1], bare_key):
                return val
            new = rest[0] + _mask(label) + rest[0] + rest[q.end():]
        else:
            stripped = rest.rstrip()
            if not _bare_is_secret(stripped, bare_key):
                return val
            new = _mask(label) + rest[len(stripped):]
        return val[:start] + new
    first = FIRST_WORD.match(val)
    if (ANNOT_TYPE.match(val.rstrip()) and ("|" in val or "[" in val)) or (first and first.group(1) in TYPE_WORDS):
        return val
    return None


def _tier2(text):
    n = 0

    def pair(m):
        nonlocal n
        ok, bare = secret_name(m.group("n"))
        if not ok or not literal_secret(m.group("v"), bare):
            return m.group(0)
        n += 1
        s, a, b = m.group(0), m.start("v") - m.start(), m.end("v") - m.start()
        return s[:a] + _mask("field " + m.group("n")) + s[b:]

    def line_field(m):
        nonlocal n
        ok, bare = secret_name(m.group("name"))
        if not ok:
            return m.group(0)
        val = m.group("val")
        stripped = val.rstrip()
        if ":" in m.group("sep"):
            new = _annotation(stripped, bare, "field " + m.group("name"))
            if new is not None:
                if new == stripped:
                    return m.group(0)
                n += 1
                return "%s%s%s%s%s%s%s" % (m.group("lead"), m.group("q"), m.group("name"), m.group("q"),
                                           m.group("sep"), new, val[len(stripped):])
        if stripped[-1:] in (";", ",") and _is_word(stripped):
            return m.group(0)       # privateKey: CryptoKey;   a word and a code ending (an UPPER=word pair: field() judges)
        if not _bare_is_secret(stripped, bare):
            return m.group(0)
        n += 1
        q = m.group("q")
        return "%s%s%s%s%s%s%s" % (m.group("lead"), q, m.group("name"), q, m.group("sep"),
                                   _mask("field " + m.group("name")), val[len(stripped):])

    def field(m):
        nonlocal n
        ok, bare = secret_name(m.group("name"))
        if not ok:
            return m.group(0)
        val = m.group("val")
        s = m.string
        prefix = s[s.rfind("\n", 0, m.start()) + 1:m.start()]
        at_line_start = not m.group("dash") and bool(LEAD_ONLY.match(prefix))
        rest = REST_OF_LINE.match(s, m.start("val")).group(0).rstrip()
        if at_line_start and ":" in m.group("sep") and _annotation(rest, bare, "x") is not None:
            return m.group(0)       # name: TYPE  (line_field already judged it)
        if (val[:1] not in "\"'\\" and HDR_FIELD_NAME.match(m.group("name"))
                and _code_header_value(VALUE_TAIL.sub("", val), "")):
            return m.group(0)       # headers: { apikey: supabaseAnonKey, ... }  same rule as the tier 1 header rules
        if val[:1] not in "\"'\\" and (CODE_KEYWORD.match(rest) or (at_line_start and CODE_OPERATOR.match(rest))):
            return m.group(0)       # token = await getToken()   token = cfg.token or env_token
        if val.startswith('\\"'):
            lq, inner, rq = '\\"', val[2:-2], '\\"'
        elif val[:1] in "\"'" and len(val) >= 2:
            lq, inner, rq = val[0], val[1:-1], val[-1]
        else:
            lq, inner, rq = "", val, ""
        if lq:
            if not literal_secret(inner, bare):
                return m.group(0)
            if WORD_VALUE.match(inner) and not _password_family(m.group("name")):
                return m.group(0)   # colorToken: 'primary'   (a password-named field still masks "hunter")
        elif not _bare_is_secret(inner, bare):
            return m.group(0)
        elif _is_word(inner) and not (m.group("dash") or (m.group("sep") == "=" and ENV_FIELD_NAME.match(m.group("name")))):
            return m.group(0)       # privateKey: CryptoKey  /  secrets=x; but not --password=hunter or PGPASSWORD=hunter
        n += 1
        return "%s%s%s%s%s%s%s%s" % (m.group("dash") or "", m.group("q"), m.group("name"), m.group("q"),
                                     m.group("sep"), lq, _mask("field " + m.group("name")), rq)
    if ":" in text or "=" in text:
        if "value" in text:
            for rx in PAIR_RES:
                text = rx.sub(pair, text)
        text = LINE_FIELD.sub(line_field, text)
        text = FIELD.sub(field, text)
    return text, n


# ---------------------------------------------------------------------------------------------- public API
_calls = 0


def _maybe_crash():
    global _calls
    _calls += 1
    if os.environ.get("SECRET_SCRUBBER_FAIL") == "1":
        raise RuntimeError("forced failure")
    after = os.environ.get("SECRET_SCRUBBER_FAIL_AFTER")
    if after and _calls > int(after):
        raise RuntimeError("forced failure")


def scrub_text(text, tier=2, needles=None):
    """(new_text, count). tier 1: known values, token shapes, key blocks, URL passwords, auth headers.
    tier 2: tier 1 plus the value of secret-named fields. needles=None loads this machine's known values."""
    _maybe_crash()
    if not text:
        return text, 0
    if needles is None:
        needles = known_needles()
    text, n = _tier1(text, needles)
    if tier >= 2:
        text, k = _tier2(text)
        n += k
    return text, n


def scrub_obj(obj, tier=2, needles=None):
    """Walk dicts, lists and tuples; scrub every string; keep the shape. (new_obj, count)."""
    if needles is None:
        needles = known_needles()
    if isinstance(obj, str):
        return scrub_text(obj, tier, needles)
    if isinstance(obj, dict):
        out, total = {}, 0
        pair_value = None       # {"name": "JWT_SECRET", "value": "..."}: the value is secret when the name is
        if tier >= 2 and isinstance(obj.get("value"), str):
            for nk in ("name", "key"):
                nv = obj.get(nk)
                if isinstance(nv, str):
                    ok, bare = secret_name(nv)
                    if ok and literal_secret(obj["value"], bare):
                        pair_value = nv
                        break
        for k, v in obj.items():
            if pair_value is not None and k == "value":
                out[k] = _mask("field " + pair_value)
                total += 1
                continue
            if tier >= 2 and isinstance(v, str) and isinstance(k, str):
                ok, bare = secret_name(k)
                if ok and literal_secret(v, bare) and "\n" not in v:
                    out[k] = _mask("field " + k)    # a structured reply: {"jwt_secret": "..."}
                    total += 1
                    continue
            out[k], c = scrub_obj(v, tier, needles)
            total += c
        return out, total
    if isinstance(obj, (list, tuple)):
        items, total = [], 0
        for v in obj:
            nv, c = scrub_obj(v, tier, needles)
            items.append(nv)
            total += c
        return (items if isinstance(obj, list) else tuple(items)), total
    return obj, 0


def withhold(obj):
    """Every non-empty string replaced by the withheld notice; shape kept."""
    if isinstance(obj, str):
        return WITHHELD if obj else obj
    if isinstance(obj, dict):
        return {k: withhold(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [withhold(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------------------------- --filter
class LineFilter:
    """Stream filter on bytes: feed(bytes) -> bytes, finish() -> bytes.
    Holds a partial line until its newline arrives (a line longer than MAX_LINE bytes is cut after the last safe blank
    in the final CUT_WINDOW bytes, or replaced by a notice with the rest of that line dropped), keeps the
    private-key-block state between lines, and after an internal error returns only withheld notices (one per line).
    flush_partial() scrubs and returns the held partial line now (for a stream that went quiet); it does nothing
    inside a private key block. Bytes that are not UTF-8 pass through unchanged (surrogateescape)."""
    MAX_LINE = 1024 * 1024
    CUT_WINDOW = 4096       # how far back from MAX_LINE a forced cut looks for a blank to cut after

    def __init__(self, tier=2):
        self.tier = tier
        self.discarding = False
        self.cut_refused = 0        # lines replaced by a notice because no safe place to cut was found
        self.buf = bytearray()
        self.in_pem = False
        self.failed = False
        self.needles = None

    def _text(self, body, end):
        if self.needles is None:
            self.needles = known_needles()
        if self.in_pem:
            m = PEM_END.search(body)
            if not m:
                return ""
            self.in_pem = False
            out, _ = scrub_text(body[m.end():], self.tier, self.needles)
            return out + end if out else ""
        m = PEM_BEGIN.search(body)
        if m and not PEM_END.search(body, m.end()):
            before, _ = scrub_text(body[:m.start()], self.tier, self.needles)
            self.in_pem = True
            return before + _mask("private-key") + end
        out, _ = scrub_text(body, self.tier, self.needles)
        return out + end

    def _one(self, raw):
        if self.failed:
            return (WITHHELD + "\n").encode()
        try:
            text = raw.decode("utf-8", "surrogateescape")
            end = "\n" if text.endswith("\n") else ""
            return self._text(text[:-1] if end else text, end).encode("utf-8", "surrogateescape")
        except Exception:
            self.failed = True
            return (WITHHELD + "\n").encode()

    def _safe_cut(self, buf, pos):
        """End index for a forced cut of an over-long line, or None. Cuts just after the last space or tab in the final
        CUT_WINDOW bytes whose previous non-blank byte is not : or =, so no token and no name: value pair is split."""
        end = pos + self.MAX_LINE
        for j in range(end - 1, max(pos, end - self.CUT_WINDOW) - 1, -1):
            if buf[j] not in b" \t":
                continue
            k = j - 1
            while k >= pos and buf[k] in b" \t":
                k -= 1
            if k < pos or buf[k] not in b":=":
                return j + 1
        return None

    def feed(self, data):
        self.buf += data
        out, pos, buf = [], 0, self.buf
        while True:
            if self.discarding:       # the rest of a line that had no safe place to cut
                i = buf.find(b"\n", pos)
                if i < 0:
                    pos = len(buf)
                    break
                pos, self.discarding = i + 1, False
                continue
            i = buf.find(b"\n", pos)
            if i >= 0:
                stop = i + 1
            elif len(buf) - pos > self.MAX_LINE:
                stop = self._safe_cut(buf, pos)
                if stop is None:
                    out.append(b"<withheld: a line over 1 MB with no safe place to cut>\n")
                    self.discarding = True
                    self.cut_refused += 1
                    continue
            else:
                break
            out.append(self._one(bytes(buf[pos:stop])))
            pos = stop
        del buf[:pos]
        return b"".join(out)

    def flush_partial(self):
        if not self.buf or (self.in_pem and not self.failed):
            return b""
        raw, self.buf = bytes(self.buf), bytearray()
        return self._one(raw)

    def finish(self):
        out = self.flush_partial()
        if self.in_pem:
            self.buf = bytearray()
        return out


def filter_stream(fd_in, out, tier=2):
    """Read raw bytes from file descriptor fd_in as they arrive, write filtered bytes to the binary file out."""
    lf = LineFilter(tier)
    while True:
        data = os.read(fd_in, 65536)
        if not data:
            break
        res = lf.feed(data)
        if res:
            out.write(res)
            out.flush()
    res = lf.finish()
    if res:
        out.write(res)
        out.flush()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--filter" not in argv:
        sys.stderr.write(__doc__.split("\n")[0] + "\nusage: secret_scrubber.py --filter [--tier 1|2]\n")
        return 2
    tier = 2
    if "--tier" in argv:
        try:
            tier = int(argv[argv.index("--tier") + 1])
        except (IndexError, ValueError):
            tier = 0
        if tier not in (1, 2):
            sys.stderr.write("--tier takes 1 or 2\n")
            return 2
    return filter_stream(0, sys.stdout.buffer, tier)


if __name__ == "__main__":
    sys.exit(main())
