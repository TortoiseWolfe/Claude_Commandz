#!/usr/bin/env python3
"""Fail when a tracked file holds an email address outside the placeholder allowlist.

This repo is public. Real addresses (his, a client's) must never be committed: code that
needs one reads it at run time from private config under ~/.config/afa. The guard stores no
real address; it allows only placeholder domains and no-reply senders, so any other
address-shaped text is a failure.

Usage: email_guard.py [REPO_DIR]     (default: current directory; scans `git ls-files`)
Output names file and line only, never the matched text, so a failing log leaks nothing.
Exit 0 clean, 1 found, 2 could not run.
"""
import re
import subprocess
import sys
from pathlib import Path

ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

# Domains (and their subdomains) that are reserved for examples or can never be a real mailbox.
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "example", "invalid", "test", "localhost", "localdomain",
                       # systemd unit names such as name@51900.service are not mailboxes
                       "service", "socket", "timer", "mount", "path"}
# Whole addresses that are public no-reply senders or well known role addresses used in docs and trailers.
ALLOWED_ADDRESSES = {"noreply@anthropic.com", "noreply@github.com"}
ALLOWED_SUFFIXES = ("@users.noreply.github.com",)
# File types that are binary or generated and cannot carry readable addresses.
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".mp3", ".mp4")


def allowed(address: str, extra=None) -> bool:
    extra = extra or {}
    low = address.lower()
    if low in ALLOWED_ADDRESSES or low.endswith(ALLOWED_SUFFIXES):
        return True
    local, domain = low.rsplit("@", 1)
    if local in {"noreply", "no-reply"}:
        return True
    labels = domain.split(".")
    if any(".".join(labels[i:]) in PLACEHOLDER_DOMAINS for i in range(len(labels))):
        return True
    return low in extra.get("addresses", ()) or any(
        ".".join(labels[i:]) in extra.get("domains", ()) for i in range(len(labels)))


def load_extra(root: Path):
    """Reviewed fixture addresses and domains from .github/email-guard-allow.txt (fake ones only; never a real mailbox)."""
    extra = {"addresses": set(), "domains": set()}
    path = root / ".github" / "email-guard-allow.txt"
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip().lower()
            if not line:
                continue
            if line.startswith("@"):
                extra["domains"].add(line[1:])
            else:
                extra["addresses"].add(line)
    return extra


def scan(root: Path):
    extra = load_extra(root)
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape")
        if rel.lower().endswith(SKIP_SUFFIXES):
            continue
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in ADDRESS.finditer(line):
                if not allowed(match.group(0), extra):
                    yield rel, number


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    try:
        hits = sorted(set(scan(root)))
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print("email_guard: could not list tracked files: %s" % type(exc).__name__, file=sys.stderr)
        return 2
    for rel, number in hits:
        print("%s:%d: email address outside the placeholder allowlist" % (rel, number))
    if hits:
        print("email_guard: %d line(s). Read real addresses at run time from ~/.config/afa; use example.org in tests." % len(hits), file=sys.stderr)
        return 1
    print("email_guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
