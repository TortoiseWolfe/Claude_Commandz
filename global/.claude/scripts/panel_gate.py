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

GITLEAKS_IMAGE = "zricethezav/gitleaks:latest"
# `stdin` is the right subcommand on gitleaks v8.30.1 (verified live 2026-09-30); exit 1 = findings.
GITLEAKS_CMD = ["docker", "run", "--rm", "-i", GITLEAKS_IMAGE, "stdin",
                "--no-banner", "--redact", "--exit-code", "1"]
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


def scan_secrets(diff, runner=None):
    """-> 'clean' | 'found' | 'unavailable'.

    Docker failures (daemon down, image pull failed) also exit 1, the same code gitleaks uses for
    findings, so a finding is only believed when gitleaks says so. Everything else is unavailable.
    Both non-clean answers end in GATE=skip; the split only keeps the reason honest."""
    runner = runner or default_runner
    try:
        rc, out = runner(GITLEAKS_CMD, diff)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if rc == 0:
        return "clean"
    if rc == 1 and "leaks found" in (out or "").lower():
        return "found"
    return "unavailable"


# ---------------------------------------------------------------- diff paths / never_send

_DIFF_GIT = re.compile(r"^diff --git a/(.*) b/(.*)$")
_PLUS_FILE = re.compile(r"^\+\+\+ b/(.*?)(?:\t.*)?$")


def diff_paths(diff):
    """File paths named by `diff --git` and `+++ b/` lines, in order, de-duplicated."""
    paths = []
    for line in diff.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            paths += [m.group(1), m.group(2)]
            continue
        m = _PLUS_FILE.match(line)
        if m:
            paths.append(m.group(1))
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def glob_match(path, pattern):
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
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}|localhost\b)")
GEO_RE = re.compile(r"(?<!\d)(?<!\d\.)(-?\d{1,3}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})(?!\d|\.\d)")
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+1[\s.-]?|1[\s.-])?(?:\(\d{3}\)[\s.-]?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\w)"
    r"|(?<![\w+])\+1\d{10}(?!\w)")
ADDR_RE = re.compile(
    r"(?<!\w)\d{1,6}[ \t]+(?:[A-Z][A-Za-z'’-]*[ \t]+){1,3}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Way|Ct|Court|Pike|Hwy|Pl|Place|Cir)\b")


def keep_email(addr):
    low = addr.lower()
    if low in KEEP_EMAIL_ADDRS:
        return True
    dom = low.rsplit("@", 1)[1]
    if dom.endswith(KEEP_EMAIL_SUFFIXES):
        return True
    return any(dom == d or dom.endswith("." + d) for d in KEEP_EMAIL_DOMAINS)


def load_terms(path):
    """Non-empty, non-# lines. A missing file is fine; any other read error propagates (fail closed)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    return [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]


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
            self.term_re = re.compile(r"(?<!\w)(?:" + alt + r")(?!\w)", re.IGNORECASE)

    def total(self):
        return sum(self.counts.values())

    def _ph(self, kind, key):
        m = self.maps[kind]
        if key not in m:
            m[key] = f"[{kind}_{len(m) + 1}]"
        self.counts[kind] += 1
        return m[key]

    def _urls(self, text):
        def sub(m):
            url = m.group(0)
            q = url.find("?")
            if q < 0:
                return url
            self.counts["URLQ"] += 1
            return url[:q]
        return URL_RE.sub(sub, text)

    def _emails(self, text):
        def sub(m):
            return m.group(0) if keep_email(m.group(0)) else self._ph("EMAIL", m.group(0).lower())
        return EMAIL_RE.sub(sub, text)

    def _geo(self, text):
        def sub(m):
            if abs(float(m.group(1))) > 90 or abs(float(m.group(2))) > 180:
                return m.group(0)
            return self._ph("GEO", re.sub(r"\s+", "", m.group(0)))
        return GEO_RE.sub(sub, text)

    def _phones(self, text):
        def sub(m):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            return self._ph("PHONE", digits)
        return PHONE_RE.sub(sub, text)

    def _addrs(self, text):
        return ADDR_RE.sub(lambda m: self._ph("ADDR", re.sub(r"\s+", " ", m.group(0)).lower()), text)

    def _terms(self, text):
        if not self.term_re:
            return text
        return self.term_re.sub(lambda m: self._ph("TERM", m.group(0).lower()), text)

    def redact(self, text):
        # URLs first so an email or phone hiding in a query string is dropped with it.
        for step in (self._urls, self._emails, self._geo, self._phones, self._addrs, self._terms):
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


def run_gate(diff, cls, never_send=(), terms=(), max_redactions=DEFAULT_MAX, scanner=None):
    """Secrets first (fail closed), then class, never_send and redaction volume.

    `scanner(diff)` returns 'clean' | 'found' | 'unavailable'; injectable so tests need no Docker."""
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
    hits = never_send_hits(diff_paths(diff), never_send)
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
    except (OSError, UnicodeDecodeError):
        return GateResult("skip", "terms file unreadable")
    return run_gate(diff, a.cls, a.never_send or [], terms, a.max_redactions, scanner)


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
