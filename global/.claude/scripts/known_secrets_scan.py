#!/usr/bin/env python3
"""Fail if any real secret from this machine's env files appears in the given files or folders.

usage: known_secrets_scan.py [--strict] [--repos DIR] [--ignore FILE] PATH...

gitleaks finds secrets by shape, so a bare password or a prefix-less token gets past it. This check
compares against the actual values instead: it reads every env file under --repos (default
~/repos), keeps the values that are secrets, and looks for each one under PATH in every form it is
likely to be written in: raw, JSON-escaped, URL-encoded and base64.

Collection fails closed, because a value it doesn't collect is a value no check will ever see:
  - env files by any common name: .env, .env.*, .env-*, *.env, .envrc, .secrets.env;
  - every reading a dotenv parser might take (inline '#', quotes, escapes, multi-line);
  - a value is a secret if its key names a secret (password, token, key, secret, auth, webhook,
    service role, ...), if a URL in it carries a password, webhook token or secret query
    parameter, or if it simply looks like one (long, high entropy, no spaces) under a key that
    isn't an identifier;
  - keys that ship to browsers (NEXT_PUBLIC_, VITE_, EXPO_PUBLIC_, publishable, anon, site key,
    client id) are public by design and skipped, except for credentials inside a URL.

It prints "<env file> <KEY>: <path>" only, never a value. Exit codes: 0 clean, 1 found, 2 error.
--ignore lists "<env file> <KEY>" pairs (names, not values) whose values are committed on purpose,
such as shared test fixtures; an ignored value is ignored wherever it appears.
"""
import argparse
import base64
import json
import math
import os
import re
import sys
import urllib.parse
from collections import Counter

SECRET_KEY = re.compile(
    r"pass(?:word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))|credential|private"
    r"|session|cookie|bearer|webhook|dsn|database_url|db_url|connection|conn_str|signing|salt"
    r"|service[_-]?role|access[_-]?key|(?:^|_)(?:key|pat|sid|pin)(?:$|_)", re.I)
PUBLIC_KEY = re.compile(r"^(?:NEXT_PUBLIC_|VITE_|EXPO_PUBLIC_|PUBLIC_|REACT_APP_|GATSBY_)|publishable"
                        r"|anon[_-]?key|site[_-]?key|client[_-]?id", re.I)
IDENT_KEY = re.compile(r"(?:_|^)(?:id|ids|ref|name|email|user|username|domain|bucket|repo|url|uri|host"
                       r"|port|region|path|dir|file|env|mode|level|version|account|zone|org)$", re.I)
# A key that holds the NAME of a secret (a Docker swarm secret, a vault path), not the secret itself.
SECRET_NAME_KEY = re.compile(r"swarm[_-]?secret|secret[_-]?(?:name|id|ref|arn|path)$", re.I)
NOT_SECRET_KEY = re.compile(r"(?:max|min)[_-]?\w*tokens|tokens?[_-](?:limit|count|used)|_ttl$|enabled$", re.I)
TRIVIAL = {"postgres", "password", "changeme", "localhost", "example", "secret", "true", "false",
           "admin", "root", "supabase", "none", "null"}
URL_PW = re.compile(r"[a-z][\w+.-]*://[^\s:/@'\"]*:([^\s@/'\"]+)@", re.I)
HOOK = re.compile(r"/webhooks?/\d+/([\w-]{20,})|hooks\.[\w.-]+/[\w-]+/([\w/-]{20,})", re.I)
URL_QUERY = re.compile(r"[?&](?:token|key|api_key|apikey|secret|sig|signature|password|pass|"
                       r"access_token|auth|code)=([^&\s#'\"]{8,})", re.I)
URL_SCHEME = re.compile(r"^[a-z][\w+.-]*://", re.I)
URL_IS_SECRET_KEY = re.compile(r"webhook|hook_url|callback|dsn", re.I)   # URLs that are secrets whole
NON_SECRET_SHAPE = re.compile(
    r"^(?:https?://[^\s@?]*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|~?/[^\s]*|\./[^\s]*|\.?[a-z0-9-]+(?:\.[a-z0-9-]+)+"
    r"|-?\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2}[T ]?[\d:.Z+-]*)$", re.I)
WALK_SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__"}
LOCAL_ONLY_DIR = re.compile(r"/supabase/\.temp(?:/|$)")   # the Supabase CLI's per-machine local-stack keys
SCAN_SKIP = WALK_SKIP | {"vendor", ".next", "dist", "build"}
ENV_NAME = re.compile(r"^(?:\.env(?:[._-][\w.-]+)?|[\w.-]+\.env|\.envrc)$")
ENV_SKIP = re.compile(r"example|sample|template|defaults$|\.dist$", re.I)


def env_files(root, depth=8):
    base = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in WALK_SKIP]
        if d.count(os.sep) - base >= depth or LOCAL_ONLY_DIR.search(d):
            dirs[:] = []
            if LOCAL_ONLY_DIR.search(d):
                continue
        for f in files:
            if ENV_NAME.match(f) and not ENV_SKIP.search(f):
                yield os.path.join(d, f)


ASSIGN_LINE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][\w.-]*)\s*=\s*(.*)$")


def env_values(text):
    """(KEY, value) for every reading of each assignment that a dotenv parser might take.
    Readers disagree (docker compose, python-dotenv and node differ on inline '#' comments and
    quotes), so where they could differ every plausible value is yielded."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = ASSIGN_LINE.match(lines[i])
        i += 1
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        vals = set()
        if raw[:1] in ("'", '"'):
            q, body, j = raw[0], raw[1:], i
            end = _closing(body, q)
            while end < 0 and j < len(lines):        # a quoted value may span lines
                body += "\n" + lines[j]
                j += 1
                end = _closing(body, q)
            if end >= 0:
                i = j
                inner = body[:end]
                vals.add(inner)
                if q == '"':
                    vals.add(inner.replace('\\"', '"').replace("\\n", "\n"))
            vals.add(raw.strip())                    # a reader that keeps the quotes
            vals.add(raw.strip().strip("'\""))
        else:
            vals.add(raw.strip())                    # '#' is literal to some readers
            vals.add(re.split(r"\s#", raw, maxsplit=1)[0].strip())   # and starts a comment to others
        for v in vals:
            if v:
                yield key, v


def _closing(body, q):
    k = 0
    while k < len(body):
        if body[k] == "\\" and q == '"':
            k += 2
            continue
        if body[k] == q:
            return k
        k += 1
    return -1


def _read(path):
    # surrogateescape keeps every byte, so a value with non-UTF-8 bytes still matches byte for byte
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "surrogateescape")


def _pairs(root, files=None):
    for p in (files if files is not None else env_files(root)):
        rel = os.path.relpath(p, root)
        try:
            text = _read(p)
        except OSError:
            continue
        for key, val in env_values(text):
            yield rel, key, val


def entropy(v):
    n = len(v)
    return -sum(c / n * math.log2(c / n) for c in Counter(v).values()) if n else 0.0


def looks_secret(v):
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return len(v) >= 24 and not re.search(r"\s", v) and entropy(v) >= 3.5 and classes >= 2 \
        and not NON_SECRET_SHAPE.match(v)


def secret_parts(key, val):
    """The secret substrings of one env assignment (possibly none)."""
    out = set()
    for rx in (URL_PW, HOOK, URL_QUERY):
        for hit in rx.finditer(val):
            s = next((g for g in hit.groups() if g), "")
            if len(s) >= 8 and s.lower() not in TRIVIAL:
                out.add(urllib.parse.unquote(s))
                out.add(s)
    if PUBLIC_KEY.search(key) or NOT_SECRET_KEY.search(key):
        return out
    bare = val.strip().strip("'\"")                  # shape tests see the value without quotes
    if not bare or bare[0] in "$`<" or bare.lower() in TRIVIAL or NON_SECRET_SHAPE.match(bare):
        return out
    if URL_SCHEME.match(bare) and not URL_IS_SECRET_KEY.search(key):
        return out    # the secret of an ordinary URL is its credential, collected above
    if SECRET_NAME_KEY.search(key) and re.fullmatch(r"[a-z0-9_.-]+", bare):
        return out    # the name of a secret, not the secret
    if SECRET_KEY.search(key) and len(val) >= 8:
        out.add(val)
    elif not IDENT_KEY.search(key) and looks_secret(val):
        out.add(val)
    return out


def collect(root, ignore, files=None):
    """value -> (env file relative to root, KEY). Values themselves never leave this function's callers.
    An ignored (file, KEY) pair ignores its VALUE everywhere: published fixtures recur in other env files.
    `files` lets a caller pass a cached env-file list instead of walking root (the walk takes ~1 s)."""
    pairs = list(_pairs(root, files))
    ignored = {val for rel, key, val in pairs if (rel, key) in ignore}
    found = {}
    for rel, key, val in pairs:
        if val in ignored:
            continue
        for s in secret_parts(key, val):
            if s not in ignored:
                found.setdefault(s, (rel, key))
    return found


def forms(value):
    """The byte strings a value is likely to be written as: raw, JSON-escaped, URL-encoded and,
    for longer values, the stable middle of its base64 encoding at each of the three alignments."""
    raw = value.encode("utf-8", "surrogateescape")
    out = {raw, json.dumps(value)[1:-1].encode("utf-8", "surrogateescape")}
    out.add(urllib.parse.quote(raw, safe="").encode())
    out.add(urllib.parse.quote_plus(raw).encode())
    if len(raw) >= 12:
        for k in range(3):
            for enc in (base64.b64encode, base64.urlsafe_b64encode):
                b = enc(b"\0" * k + raw).rstrip(b"=")
                start = -(-4 * k // 3)                   # chars touched by the k prefix bytes
                stable = b[start:len(b) - 3]             # drop the tail that depends on what follows
                if len(stable) >= 12:
                    out.add(stable)
    return out


def needles_for(secrets):
    """[(bytes, (env file, KEY))] for every form of every collected value."""
    return [(f, meta) for v, meta in secrets.items() for f in forms(v)]


def files_under(paths, skip_dirs=True):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for d, dirs, files in os.walk(p):
            if skip_dirs:
                dirs[:] = [x for x in dirs if x not in SCAN_SKIP]
            for f in files:
                yield os.path.join(d, f)


def contains(path, needles, chunk=8 * 1024 * 1024):
    """Return the metas of needles found in path, reading in overlapping chunks (no size limit)."""
    overlap = max(len(n) for n, _ in needles)
    found, tail = {}, b""
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            buf = tail + block
            for needle, meta in needles:
                if meta not in found and needle in buf:
                    found[meta] = True
            tail = buf[-overlap:]
    return list(found)


def load_ignore(path):
    pairs = set()
    if path and os.path.exists(path):
        for line in open(path):
            parts = line.split("#")[0].split()
            if len(parts) == 2:
                pairs.add((parts[0], parts[1]))
    return pairs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fail if a real env-file secret appears under PATH.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--repos", default=os.path.expanduser("~/repos"))
    ap.add_argument("--ignore", default=os.path.expanduser("~/.config/known-secrets/ignore.txt"))
    ap.add_argument("--strict", action="store_true",
                    help="for push checks: walk every folder, and treat an unreadable file as a finding")
    a = ap.parse_args(argv)
    secrets = collect(a.repos, load_ignore(a.ignore))
    if not secrets:
        print("known-secrets: no secret values found under %s; nothing to check against" % a.repos, file=sys.stderr)
        return 2
    needles = needles_for(secrets)
    repos_real = os.path.realpath(a.repos) + os.sep
    hits = 0
    for f in files_under(a.paths, skip_dirs=not a.strict):
        if os.path.realpath(f).startswith(repos_real) and ENV_NAME.match(os.path.basename(f)):
            continue  # the env files themselves
        if os.path.islink(f) or not os.path.isfile(f):
            continue
        try:
            metas = contains(f, needles)
        except OSError:
            if a.strict:
                print("unreadable (counted as a finding in --strict): %s" % f)
                hits += 1
            continue
        for rel, key in metas:
            print("%s %s: %s" % (rel, key, f))
            hits += 1
    if hits:
        print("known-secrets: %d finding(s) (names above, values never printed)" % hits, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
