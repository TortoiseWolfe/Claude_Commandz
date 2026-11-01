#!/usr/bin/env python3
"""Replace every real env-file secret value in the given files or folders with <redacted:KEY>.

usage: scrub_known_secrets.py [--min-age SECONDS] [--repos DIR] [--secrets-dir DIR] [--ignore FILE] [--dry-run] PATH...

For session logs and file-history snapshots: a value that once reached a log stays readable there
until it's scrubbed. Values come from known_secrets_scan.collect (the env files under --repos and
the secret files under --secrets-dir);
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


def shortlist(paths, values):
    """grep -F does the 7 GB pass with patterns streamed through stdin, never a secret file."""
    patterns = b"\n".join(values) + b"\n"
    r = subprocess.run(["/usr/bin/grep", "-rlaF", "-f", "-", *paths], input=patterns, capture_output=True)
    return [os.fsdecode(f) for f in r.stdout.splitlines() if f]


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
    ap = argparse.ArgumentParser(description="Redact collected secret values in place.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--min-age", type=int, default=120)
    ap.add_argument("--repos", default=os.path.expanduser("~/repos"))
    ap.add_argument("--secrets-dir", default=os.environ.get("KNOWN_SECRETS_DIR") or
                    os.path.expanduser("~/.config/afa/secrets"))
    ap.add_argument("--ignore", default=os.path.expanduser("~/.config/known-secrets/ignore.txt"))
    ap.add_argument("--scratch", default=tempfile.gettempdir(),
                    help="retained for compatibility; secret patterns are streamed through memory")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    paths = [p for p in a.paths if os.path.exists(p)]
    if not paths:
        return 0
    secrets = ks.collect(a.repos, ks.load_ignore(a.ignore), secrets_dir=a.secrets_dir)
    if not secrets:
        print("scrub: no secret values to look for", file=sys.stderr)
        return 2
    nds = needles(secrets)
    cutoff = time.time() - a.min_age
    total, files, skipped = Counter(), 0, 0
    for f in shortlist(paths, sorted({n for n, _, _ in nds})):
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
