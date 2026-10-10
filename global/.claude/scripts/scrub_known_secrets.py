#!/usr/bin/env python3
"""Replace every real env-file secret value in the given files or folders with <redacted:KEY>.

usage: scrub_known_secrets.py [--min-age SECONDS] [--repos DIR] [--ignore FILE] [--dry-run] PATH...

For session logs and file-history snapshots: a value that once reached a log stays readable there
until it's scrubbed. Values come from known_secrets_scan.collect (the env files under --repos);
each is matched both raw and JSON-escaped, so .jsonl lines stay valid JSON. Files are rewritten
atomically with their mode and mtime kept. Files modified less than --min-age seconds ago
(default 120) are skipped, because a running session may still be appending to them.
Prints key names and counts only, never a value.
"""
import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import known_secrets_scan as ks  # noqa: E402

MAX_BYTES = 512 * 1024 * 1024


def needles(secrets):
    out = []
    for v, (rel, key) in secrets.items():
        rep = ("<redacted:%s>" % key).encode()
        out += [(f, rep, key) for f in ks.forms(v)]   # raw, JSON-escaped, URL-encoded, base64
    return sorted(out, key=lambda n: -len(n[0]))


def shortlist(paths, values, scratch):
    """grep -F does the 7 GB pass; Python only opens files that matched."""
    fd, pat = tempfile.mkstemp(dir=scratch, prefix=".scrub-patterns-")
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\n".join(values) + b"\n")
        r = subprocess.run(["/usr/bin/grep", "-rlaF", "-f", pat, *paths], capture_output=True, text=True)
        return [f for f in r.stdout.splitlines() if f]
    finally:
        os.remove(pat)


def scrub_file(path, nds, dry):
    data = open(path, "rb").read()
    counts = Counter()
    for needle, rep, key in nds:
        n = data.count(needle)
        if n:
            data = data.replace(needle, rep)
            counts[key] += n
    if counts and not dry:
        st = os.stat(path)
        d = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".scrub-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Redact real env-file secret values in place.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--min-age", type=int, default=120)
    ap.add_argument("--repos", default=os.path.expanduser("~/repos"))
    ap.add_argument("--ignore", default=os.path.expanduser("~/.config/known-secrets/ignore.txt"))
    ap.add_argument("--scratch", default=tempfile.gettempdir())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    paths = [p for p in a.paths if os.path.exists(p)]
    if not paths:
        return 0
    secrets = ks.collect(a.repos, ks.load_ignore(a.ignore))
    if not secrets:
        print("scrub: no secret values to look for", file=sys.stderr)
        return 2
    nds = needles(secrets)
    cutoff = time.time() - a.min_age
    total, files, skipped = Counter(), 0, 0
    for f in shortlist(paths, sorted({n for n, _, _ in nds}), a.scratch):
        try:
            st = os.stat(f)
        except OSError:
            continue
        if st.st_size > MAX_BYTES or not stat.S_ISREG(st.st_mode):
            continue
        if st.st_mtime > cutoff:
            skipped += 1
            continue
        c = scrub_file(f, nds, a.dry_run)
        if c:
            files += 1
            total.update(c)
    verb = "would scrub" if a.dry_run else "scrubbed"
    print("scrub: %s %d file(s), %d value(s); skipped %d file(s) modified in the last %ds"
          % (verb, files, sum(total.values()), skipped, a.min_age))
    for key, n in total.most_common():
        print("  %s: %d" % (key, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
