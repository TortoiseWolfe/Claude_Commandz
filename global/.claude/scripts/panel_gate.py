#!/usr/bin/env python3
"""Privacy gate for the free review panel: decide what may leave the machine, and redact it.

  panel_gate.py --diff FILE --class public|own|client [--never-send GLOB ...]
                [--terms-file PATH] [--max-redactions N] --out REDACTED_FILE
Prints __GATE=send|local|skip__, __REDACTED=<n>__, __GATE_REASON=<why>__ and a per-kind count
line. NEVER prints an original redacted value (counts and placeholders only). Secrets are scanned
with gitleaks in Docker and fail closed. Stdlib only."""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

# Pinned by digest (the v8.30.1 image pulled locally 2026-10-01), so a moved `latest` tag cannot swap the scanner.
GITLEAKS_IMAGE = "zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f"
# `stdin` is the right subcommand on gitleaks v8.30.1 (verified live 2026-09-30); exit 1 = findings.
# The container gets no network, a read-only root, no capabilities and no privilege escalation.
GITLEAKS_CMD = ["docker", "run", "--rm", "-i", "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", GITLEAKS_IMAGE, "stdin",
                "--no-banner", "--redact", "--exit-code", "1"]
ALLOW_MARKER_RE = re.compile(r"gitleaks\s*:\s*allow", re.IGNORECASE)
SCAN_TIMEOUT = 180
DEFAULT_TERMS = "~/.config/panel/pii-terms.txt"
DEFAULT_MAX = 20
KINDS = ("EMAIL", "PHONE", "GEO", "ADDR", "URLQ", "TERM")
KEEP_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "test.com", "localhost")
KEEP_EMAIL_SUFFIXES = (".test", ".invalid")
KEEP_EMAIL_ADDRS = ("noreply@anthropic.com",)


# ---------------------------------------------------------------- secret scan

def default_runner(cmd, text, timeout=SCAN_TIMEOUT):
    p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def scan_secrets(text, runner=None):
    """-> 'clean' | 'found' | 'unavailable'. Scans ANY text (a diff, a spec, acceptance criteria).

    Docker failures (daemon down, image pull failed) also exit 1, the same code gitleaks uses for
    findings, so a finding is only believed when gitleaks says so. A clean result needs exit 0 AND
    gitleaks' own "no leaks found" line; anything else is unavailable. Non-clean answers end in
    GATE=skip; the split only keeps the reason honest. Any `gitleaks:allow` marker is stripped first,
    because text under review must not be able to switch the scanner off."""
    runner = runner or default_runner
    text = ALLOW_MARKER_RE.sub("", text)
    try:
        rc, out = runner(GITLEAKS_CMD, text)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    low = (out or "").lower()
    if rc == 0:
        return "clean" if "no leaks found" in low else "unavailable"
    if rc == 1 and "leaks found" in low and "no leaks found" not in low:
        return "found"
    return "unavailable"


# ---------------------------------------------------------------- diff paths / never_send

_DIFF_GIT_PREFIX = "diff --git "
_PREFIXES = ("a/", "b/", "c/", "w/", "i/", "o/", "1/", "2/")   # git's default and mnemonic prefixes
_UNQUOTE_ESC = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}


def _unquote(body):
    """Undo git's C-style quoting (\\t, \\", \\\\, octal \\303\\251 bytes) for the text between the quotes."""
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "0123":
                digits = body[i + 1:i + 4]
                if len(digits) == 3 and all(c in "01234567" for c in digits):
                    out.append(int(digits, 8))
                    i += 4
                    continue
            if nxt in _UNQUOTE_ESC:
                out.append(_UNQUOTE_ESC[nxt])
                i += 2
                continue
        out += ch.encode("utf-8", "replace")
        i += 1
    return out.decode("utf-8", "replace")


def _tokens(rest):
    """Split the tail of a header line into path tokens: "quoted" (unescaped) or bare, whitespace-separated."""
    toks, i, n = [], 0, len(rest)
    while i < n:
        if rest[i] in " \t":
            i += 1
        elif rest[i] == '"':
            j = i + 1
            while j < n and rest[j] != '"':
                j += 2 if rest[j] == "\\" else 1
            toks.append(_unquote(rest[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and rest[j] not in " \t":
                j += 1
            toks.append(rest[i:j])
            i = j
    return toks


def _strip_prefix(tok):
    return tok[2:] if tok[:2] in _PREFIXES else tok


def _git_header_tokens(rest):
    toks = _tokens(rest)
    n = len(rest)
    if '"' not in rest and len(toks) != 2 and n % 2 == 1 and rest[n // 2] == " " \
            and _strip_prefix(rest[:n // 2]) == _strip_prefix(rest[n // 2 + 1:]):
        return [rest[:n // 2], rest[n // 2 + 1:]]   # unquoted paths with spaces: git writes both halves alike
    return toks


def diff_paths(diff, raw=False):
    """File paths named by `diff --git` lines (any prefix scheme, quoted or not) and `+++ `/`--- ` lines,
    in order, de-duplicated. With raw=True the unstripped tokens are listed too (used for never_send,
    where over-matching only costs a local run). /dev/null is skipped."""
    paths = []
    for line in diff.splitlines():
        if line.startswith(_DIFF_GIT_PREFIX):
            toks = _git_header_tokens(line[len(_DIFF_GIT_PREFIX):])
        elif line.startswith(("+++ ", "--- ")):
            rest = line[4:]
            if not rest.startswith('"'):
                rest = rest.split("\t", 1)[0]
            toks = _tokens(rest)[:1]
        else:
            continue
        for t in toks:
            if t == "/dev/null":
                continue
            if raw and t != _strip_prefix(t):
                paths.append(t)
            paths.append(_strip_prefix(t))
    seen = set()
    return [p for p in paths if p and not (p in seen or seen.add(p))]


def glob_match(path, pattern):
    path, pattern = path.lower(), pattern.lower()   # filesystems here may be case-insensitive
    pats = {pattern}
    if pattern.startswith("**/"):
        pats.add(pattern[3:])
    base = path.rsplit("/", 1)[-1]
    for p in pats:
        if p.endswith("/"):  # a bare directory: everything under it
            if path.startswith(p) or ("/" + p) in ("/" + path):
                return True
            continue
        if fnmatch.fnmatchcase(path, p):
            return True
        if "/" not in p and fnmatch.fnmatchcase(base, p):
            return True
    return False


def never_send_hits(paths, globs):
    return [p for p in paths if any(glob_match(p, g) for g in globs)]


# ---------------------------------------------------------------- redaction

URL_RE = re.compile(r"""https?://[^\s"'<>`)\]]+""")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+(?:@|%40)(?:(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|localhost\b)")
# Either order: lat,lng or GeoJSON [lng, lat]; the range check in _geo decides.
GEO_RE = re.compile(r"(?<!\d)(?<!\d\.)(-?\d{1,3}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})(?!\d|\.\d)")
# {"lat": 35.045, "lng": -85.309}, latitude=..., lon: ... (3+ decimals, about 100 m, so `lat: 0` survives)
GEO_KEY_RE = re.compile(r"""(["']?\b(?:lat|latitude|lng|lon|longitude)["']?\s*[:=]\s*)(-?\d{1,3}\.\d{3,})(?!\d)""",
                        re.IGNORECASE)
# Phones. Bare 10 digits only when shaped like a NANP number (area code and exchange start 2-9), which
# skips the 10-digit unix timestamps in use now (17xxxxxxxx). Tradeoff: a 10-digit id starting 2-9 with a
# 2-9 in the fourth place still matches. International `+<cc> ...` needs 8-15 digits in total.
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+1[\s.-]?|1[\s.-])?(?:\(\d{3}\)[\s.-]?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)"
    r"|(?<![\w+])\+1\d{10}(?!\w)"
    r"|(?<![\w.+-])[2-9]\d{2}[2-9]\d{6}(?![\w]|[.-]\d)"
    r"|(?<![\w+])\+[1-9]\d{0,2}(?:[ .-]?\(?\d{1,5}\)?){1,5}(?!\w)")
_SUFFIXES = ("St", "Street", "Ave", "Avenue", "Rd", "Road", "Dr", "Drive", "Ln", "Lane", "Blvd", "Way",
             "Ct", "Court", "Pike", "Hwy", "Pl", "Place", "Cir")
ADDR_RE = re.compile(
    r"(?<!\w)\d{1,6}[ \t]+(?:[A-Z][A-Za-z'’-]*[ \t]+){1,3}"
    r"(?:" + "|".join(_SUFFIXES + tuple(x.upper() for x in _SUFFIXES)) + r")\b")
USERINFO_RE = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@\"'<>`?#]*(?::[^/\s@\"'<>`]*)?)@")
FRAGMENT_RE = re.compile(r"#(?:access_token|id_token|refresh_token|code|token)=[^\s\"'<>`)\]]*")
AUTHOR_RE = re.compile(
    r"^([ \t+-]*(?:Author|Co-authored-by|Signed-off-by|Reviewed-by|Tested-by|Committer|From)[ \t]*:[ \t]*)"
    r"([^<\n]*?[^<\s][^<\n]*?)([ \t]*<[^<>\n@]+@[^<>\n]+>)",
    re.IGNORECASE | re.MULTILINE)


def keep_email(addr):
    low = addr.lower().replace("%40", "@")
    if low in KEEP_EMAIL_ADDRS:
        return True
    dom = low.rsplit("@", 1)[1]
    if dom.endswith(KEEP_EMAIL_SUFFIXES):
        return True
    return any(dom == d or dom.endswith("." + d) for d in KEEP_EMAIL_DOMAINS)


def load_terms(path):
    """Non-empty, non-# lines. A missing file gives []; any other read error propagates (fail closed).
    Use terms_file_present() to tell "missing" from "present but empty": the gate treats them differently."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    return [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]


def terms_file_present(path):
    return os.path.exists(os.path.expanduser(path))


def _term_bounded(text, s, e):
    """A term hit counts when no LETTER touches it, so `_`, digits, punctuation and camelCase humps are
    boundaries (acme_corp, AcmeClient, getAcmeUser()) while `macmeal` is not a hit for `acme`."""
    left = s == 0 or not text[s - 1].isalpha() or (text[s - 1].islower() and text[s].isupper())
    right = e == len(text) or not text[e].isalpha() or (text[e].isupper() and text[e - 1].islower())
    return left and right


class Redactor:
    """Replaces matches with stable placeholders. Originals live only in these in-memory maps;
    nothing here, and nothing that calls it, may print them."""

    def __init__(self, terms=()):
        self.maps = {k: {} for k in KINDS}
        self.counts = {k: 0 for k in KINDS}
        terms = sorted({t for t in terms if t}, key=len, reverse=True)
        self.term_re = None
        if terms:
            alt = "|".join(re.escape(t) for t in terms)
            self.term_re = re.compile(r"(?:" + alt + r")", re.IGNORECASE)

    def total(self):
        return sum(self.counts.values())

    def _ph(self, kind, key):
        m = self.maps[kind]
        if key not in m:
            m[key] = f"[{kind}_{len(m) + 1}]"
        self.counts[kind] += 1
        return m[key]

    def _urls(self, text):
        def userinfo(m):
            if not m.group(2):
                return m.group(0)
            self.counts["URLQ"] += 1
            return m.group(1) + "[USERINFO]@"

        def sub(m):
            url = m.group(0)
            q = url.find("?")
            if q < 0:
                return url
            self.counts["URLQ"] += 1
            return url[:q]

        def frag(m):
            self.counts["URLQ"] += 1
            return ""
        text = USERINFO_RE.sub(userinfo, text)
        return FRAGMENT_RE.sub(frag, URL_RE.sub(sub, text))

    def _authors(self, text):
        def sub(m):
            if keep_email(m.group(3).strip()[1:-1]):
                return m.group(0)
            return m.group(1) + self._ph("TERM", "name:" + m.group(2).lower()) + m.group(3)
        return AUTHOR_RE.sub(sub, text)

    def _emails(self, text):
        def sub(m):
            return m.group(0) if keep_email(m.group(0)) else self._ph("EMAIL", m.group(0).lower().replace("%40", "@"))
        return EMAIL_RE.sub(sub, text)

    def _geo(self, text):
        def sub(m):
            a, b = abs(float(m.group(1))), abs(float(m.group(2)))
            if not ((a <= 90 and b <= 180) or (a <= 180 and b <= 90)):
                return m.group(0)
            return self._ph("GEO", re.sub(r"\s+", "", m.group(0)))

        def keyed(m):
            return m.group(1) + self._ph("GEO", m.group(2))
        return GEO_KEY_RE.sub(keyed, GEO_RE.sub(sub, text))

    def _phones(self, text):
        def sub(m):
            digits = re.sub(r"\D", "", m.group(0))
            if m.group(0).startswith("+") and not (8 <= len(digits) <= 15):
                return m.group(0)   # +1.2.3 and similar are not numbers
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            return self._ph("PHONE", digits)
        return PHONE_RE.sub(sub, text)

    def _addrs(self, text):
        return ADDR_RE.sub(lambda m: self._ph("ADDR", re.sub(r"\s+", " ", m.group(0)).lower()), text)

    def _terms(self, text):
        if not self.term_re:
            return text
        out, pos, last = [], 0, 0
        while True:
            m = self.term_re.search(text, pos)
            if not m:
                break
            if _term_bounded(text, m.start(), m.end()):
                out += [text[last:m.start()], self._ph("TERM", m.group(0).lower())]
                last = pos = m.end()
            else:
                pos = m.start() + 1   # a shorter or later overlapping hit may still be bounded
        return "".join(out) + text[last:]

    def redact(self, text):
        # URLs first so an email or phone hiding in a query string is dropped with it.
        for step in (self._urls, self._authors, self._emails, self._geo, self._phones, self._addrs, self._terms):
            text = step(text)
        return text


# ---------------------------------------------------------------- the gate

@dataclass
class GateResult:
    gate: str                      # send | local | skip
    reason: str
    text: str = ""                 # the redacted diff (empty on skip: nothing may leave)
    count: int = 0
    kinds: dict = field(default_factory=lambda: {k: 0 for k in KINDS})


def run_gate(diff, cls, never_send=(), terms=(), max_redactions=DEFAULT_MAX, scanner=None,
             terms_missing=False):
    """Secrets first (fail closed), then class, never_send and redaction volume.

    `scanner(diff)` returns 'clean' | 'found' | 'unavailable'; injectable so tests need no Docker.
    `terms_missing`: the PII terms file does not exist. Without it nothing can catch client or own names,
    so those classes stay local; `public` carries no private names and proceeds. An empty file is present."""
    try:
        status = (scanner or scan_secrets)(diff)
    except Exception:  # a broken scanner is an unavailable scanner
        status = "unavailable"
    if status != "clean":
        return GateResult("skip", "secret scan found a finding" if status == "found"
                          else "secret scan unavailable")
    red = Redactor(terms)
    text = red.redact(diff)
    count = red.total()
    reasons = []
    if cls == "client":
        reasons.append("client class")
    if terms_missing and cls in ("own", "client"):
        reasons.append("no terms file")
    paths = diff_paths(diff, raw=True)
    if not paths and re.search(r"^@@ ", diff, re.MULTILINE):
        reasons.append("unparsed paths")   # hunks but no file name: never_send cannot be checked
    hits = never_send_hits(paths, never_send)
    if hits:
        reasons.append(f"never_send match ({len(hits)} path{'' if len(hits) == 1 else 's'})")
    if count > max_redactions:
        reasons.append(f"{count} redactions > max {max_redactions}")
    return GateResult("local" if reasons else "send", "; ".join(reasons) or "ok",
                      text, count, dict(red.counts))


def sentinel_lines(res):
    kinds = ",".join(f"{k}:{res.kinds.get(k, 0)}" for k in KINDS)
    return [f"__GATE={res.gate}__", f"__REDACTED={res.count}__",
            f"__GATE_REASON={res.reason}__", f"__REDACT_KINDS={kinds}__"]


CLASSES_PATH = "~/.config/panel/classes.json"   # {"repos": {"<name>": "public|own|client"}, ...}
_STRICTNESS = {"public": 0, "own": 1, "client": 2}


def stricter(a, b):
    """The stricter of two classes (client > own > public). An unknown class counts as client."""
    ra, rb = _STRICTNESS.get(a, 2), _STRICTNESS.get(b, 2)
    return a if ra >= rb else b


def listed_class(repo, path=None):
    """The class classes.json lists for `repo` (matched by name, case-insensitively, last path part).
    None only when the file is missing or no repo name was given (the passed class then stands). A repo
    the file does not list is `client`, as the file's comment says; a file that exists but cannot be
    read or parsed fails closed too: every repo is then `client`."""
    import json
    try:
        with open(os.path.expanduser(path or CLASSES_PATH), encoding="utf-8") as f:
            repos = json.load(f)["repos"]
        if not isinstance(repos, dict):
            raise ValueError("repos")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return "client"
    if not repo:
        return None
    name = str(repo).rstrip("/").rsplit("/", 1)[-1].lower()
    for k, v in repos.items():
        if str(k).lower() == name:
            return v if v in _STRICTNESS else "client"
    return "client"   # the file's own rule: a repo it doesn't list is client (only the local model sees it)


def effective_class(repo, cls, path=None):
    """The stricter of the class passed in and the one classes.json lists for `repo`."""
    listed = listed_class(repo, path)
    return cls if listed is None else stricter(cls, listed)


def write_private(path, text):
    fd = os.open(os.path.expanduser(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def gate_from_args(a, scanner=None):
    """Run the gate for parsed args. Fails closed: unreadable inputs mean skip, never send."""
    try:
        with open(os.path.expanduser(a.diff), encoding="utf-8", errors="replace") as f:
            diff = f.read()
    except OSError:
        return GateResult("skip", "diff unreadable")
    try:
        terms = load_terms(a.terms_file)
        missing = not terms_file_present(a.terms_file)
    except (OSError, UnicodeDecodeError):
        return GateResult("skip", "terms file unreadable")
    return run_gate(diff, a.cls, a.never_send or [], terms, a.max_redactions, scanner, missing)


def build_parser():
    ap = argparse.ArgumentParser(description="Privacy gate and redactor for the review panel.")
    ap.add_argument("--diff", required=True)
    ap.add_argument("--class", dest="cls", required=True, choices=("public", "own", "client"))
    ap.add_argument("--never-send", action="append", default=[], metavar="GLOB", nargs="+")
    ap.add_argument("--terms-file", default=DEFAULT_TERMS)
    ap.add_argument("--max-redactions", type=int, default=DEFAULT_MAX)
    ap.add_argument("--out", required=True)
    return ap


def parse_args(argv=None):
    a = build_parser().parse_args(argv)
    a.never_send = [g for group in a.never_send for g in group]  # accepts repeated and multi-value
    return a


def main(argv=None, scanner=None):
    a = parse_args(argv)
    res = gate_from_args(a, scanner)
    try:
        write_private(a.out, "" if res.gate == "skip" else res.text)
    except OSError:
        res = GateResult("skip", "output unwritable")
    for line in sentinel_lines(res):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
