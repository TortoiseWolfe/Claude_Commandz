#!/usr/bin/env python3
"""Fail if any real secret from this machine's env files appears in the given files or folders.

usage: known_secrets_scan.py [--repos DIR] [--ignore FILE] PATH...

gitleaks finds secrets by shape, so a bare password or a webhook token with no prefix gets past
it, for example a password pasted into a synced note. This check compares against the actual
values instead: it reads every
env file under --repos (default ~/repos), keeps the values that are secrets, and looks for them
byte for byte under each PATH.

It prints "<env file> <KEY>: <path>" only, never a value. Exit codes: 0 clean, 1 found, 2 error.
--ignore lists "<env file> <KEY>" pairs (names, not values) that are committed on purpose, such as
shared test fixtures. The default is ~/.config/known-secrets/ignore.txt.
"""
import argparse
import json
import os
import re
import sys

WORD = re.compile(r"pass(?:word|wd)?|secret|token|api[_-]?key|apikey|auth(?!or(?!iz))|credential"
                  r"|private[_-]?key|session|cookie|bearer|webhook|dsn|database_url|db_url", re.I)
SAFE_KEY = re.compile(r"(?:max|min)[_-]?\w*tokens|tokens?[_-](?:limit|count|used)|_ttl$|_path$|_file$"
                      r"|enabled$|client_?id|publishable|anon_key|site_key", re.I)
LOCAL = re.compile(r"localhost|127\.0\.0\.1|host\.docker\.internal|0\.0\.0\.0")
TRIVIAL = {"postgres", "password", "changeme", "localhost", "example", "secret", "true", "false",
           "admin", "root", "supabase"}
URL_PW = re.compile(r"[a-z][\w+.-]*://[^\s:/@'\"]*:([^\s@/'\"]+)@", re.I)
HOOK = re.compile(r"/webhooks?/\d+/([\w-]{20,})|hooks\.slack\.com/services/([\w/]{20,})", re.I)
SKIP_DIRS = {"node_modules", ".git", "vendor", ".venv", "venv", "__pycache__", ".next", "dist", "build"}
ENV_NAME = re.compile(r"^\.env(?:\.[\w.-]+)?$")
ENV_SKIP = re.compile(r"example|sample|template|\.bak|defaults$")


def env_files(root, depth=5):
    base = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        for f in files:
            if ENV_NAME.match(f) and not ENV_SKIP.search(f):
                yield os.path.join(d, f)


def _pairs(root, files=None):
    for p in (files if files is not None else env_files(root)):
        rel = os.path.relpath(p, root)
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][\w.-]*)\s*=\s*(.*)$", line)
            if m:
                yield rel, m.group(1), m.group(2).strip().split(" #")[0].strip().strip('"').strip("'")


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
        for rx in (URL_PW, HOOK):
            for hit in rx.finditer(val):
                s = next(g for g in hit.groups() if g)
                if len(s) >= 8 and s.lower() not in TRIVIAL:
                    found.setdefault(s, (rel, key))
        if (WORD.search(key) and not SAFE_KEY.search(key) and len(val) >= 12
                and val[0] not in "$`<" and not LOCAL.search(val)):
            found.setdefault(val, (rel, key))
    return found


def files_under(paths, skip_dirs=True):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for d, dirs, files in os.walk(p):
            if skip_dirs:
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
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
    needles = []
    for s_, meta in secrets.items():
        for form in {s_.encode(), json.dumps(s_)[1:-1].encode()}:
            needles.append((form, meta))
    hits = 0
    for f in files_under(a.paths, skip_dirs=not a.strict):
        if os.path.realpath(f).startswith(os.path.realpath(a.repos) + os.sep) and ENV_NAME.match(os.path.basename(f)):
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
