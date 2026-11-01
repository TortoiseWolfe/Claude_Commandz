#!/usr/bin/env python3
"""Run a process listing and redact known and recognizable secrets from its output."""
import os
import subprocess
import sys

import known_secrets_scan as ks
import redact_view


def redact_text(text):
    """Redact collected secret values and recognizable credential forms without printing them."""
    repos = os.environ.get("KNOWN_SECRETS_REPOS") or os.path.expanduser("~/repos")
    secrets_dir = os.environ.get("KNOWN_SECRETS_DIR") or os.path.expanduser("~/.config/afa/secrets")
    ignore = os.environ.get("KNOWN_SECRETS_IGNORE") or os.path.expanduser("~/.config/known-secrets/ignore.txt")
    found = ks.collect(repos, ks.load_ignore(ignore), secrets_dir=secrets_dir)
    data = text.encode("utf-8", "surrogateescape")
    replacements = []
    for value, (_, key) in found.items():
        replacement = ("<redacted:%s>" % key).encode("utf-8", "replace")
        replacements.extend((needle, replacement) for needle in ks.forms(value) if needle)
    for needle, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        data = data.replace(needle, replacement)
    text = data.decode("utf-8", "replace")
    return "\n".join(redact_view.scrub(line) for line in text.split("\n"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--":
        argv.pop(0)
    if not argv:
        print("ps_redacted: provide a process-listing command", file=sys.stderr)
        return 2
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    except OSError as exc:
        print("ps_redacted: could not run command (%s)" % (exc.strerror or "execution failed"), file=sys.stderr)
        return 127
    output = result.stdout.decode("utf-8", "replace")
    sys.stdout.write(redact_text(output))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
