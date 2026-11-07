#!/usr/bin/env python3
"""One 1-token call on the Console worker key: does the Console have credit right now? No model reads the answer.

Prints only the HTTP status and the API's error message (cut to 160 characters), never the key. If the message ever
contained the key, that part is replaced with "[key]" before printing.

Where the key comes from, in order:
  1. the environment variable ANTHROPIC_WORKER_KEY
  2. the file named by CONSOLE_PROBE_KEY_FILE, or ~/.config/anthropic/worker-key when that is not set
Run it isolated, as the rules name it:  python3 -I ~/.claude/scripts/console_probe.py
Exit codes: 0 = the Console answered 200; 1 = it answered with an error status; 2 = no key found; 3 = no answer (network).
Test hook: CONSOLE_PROBE_URL may replace the API address, but only with a http://127.0.0.1 address, so the key can never be
sent anywhere else by setting a variable.
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_KEY_FILE = pathlib.Path.home() / ".config/anthropic/worker-key"


def find_key():
    key = os.environ.get("ANTHROPIC_WORKER_KEY", "").strip()
    if key:
        return key
    path = pathlib.Path(os.environ.get("CONSOLE_PROBE_KEY_FILE") or DEFAULT_KEY_FILE)
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def api_url():
    url = os.environ.get("CONSOLE_PROBE_URL", "")
    return url if url.startswith("http://127.0.0.1") else DEFAULT_URL


def scrub(text, key):
    return text.replace(key, "[key]") if key else text


def main():
    key = find_key()
    if not key:
        print("no Console key: set ANTHROPIC_WORKER_KEY or put it in the key file")
        return 2
    body = {"model": "claude-haiku-5-5", "max_tokens": 1,
            "messages": [{"role": "user", "content": "ok"}]}
    req = urllib.request.Request(
        api_url(),
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        print("HTTP", r.status, "OK: the Console key answers")
        return 0
    except urllib.error.HTTPError as e:
        try:
            b = json.loads(e.read() or b"{}")
        except ValueError:
            b = {}
        err = b.get("error") if isinstance(b, dict) else None
        msg = str(err.get("message", "")) if isinstance(err, dict) else ""
        print("HTTP", e.code, scrub(msg, key)[:160])
        return 1
    except (urllib.error.URLError, OSError) as e:
        print("no answer from the Console:", scrub(str(getattr(e, "reason", e)), key)[:160])
        return 3


if __name__ == "__main__":
    sys.exit(main())
