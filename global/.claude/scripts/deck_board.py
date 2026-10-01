#!/usr/bin/env python3
"""Read-only view of a Nextcloud Deck board: stacks and cards, never credentials.

    deck_board.py --env <repo>/.env --prefix NEXTCLOUD_PROD --board <id> [--url URL]

Reads <PREFIX>_URL, <PREFIX>_USER and <PREFIX>_PASS from the env file inside this process; the
password is never printed, logged or put on a command line. GET requests only. Prints the board
title, then each stack with its cards: id, title, assignees, labels, due date, last change, and
the first 200 characters of the description.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone


def read_env(path, prefix):
    vals = {}
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            if k in (f"{prefix}_URL", f"{prefix}_USER", f"{prefix}_PASS"):
                vals[k[len(prefix) + 1:]] = v.strip().strip('"').strip("'")
    missing = [k for k in ("URL", "USER", "PASS") if not vals.get(k)]
    if missing:
        sys.exit(f"deck_board: {prefix}_{'/'.join(missing)} not set in the env file")
    return vals


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # never resend the auth header somewhere else
        return None


def get(base, user, pw, path):
    req = urllib.request.Request(base.rstrip("/") + path, headers={
        "OCS-APIRequest": "true", "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()})
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def when(v):
    if not v:
        return ""
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%d")
        return str(v)[:10]
    except Exception:
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--prefix", default="NEXTCLOUD_PROD")
    ap.add_argument("--board", type=int, required=True)
    ap.add_argument("--url", help="override the base URL (e.g. a staging host)")
    a = ap.parse_args(argv)
    c = read_env(a.env, a.prefix)
    base = a.url or c["URL"]
    st, board = get(base, c["USER"], c["PASS"], f"/index.php/apps/deck/api/v1.1/boards/{a.board}")
    if st != 200:
        print(f"__DECK=error__ board {a.board} at {base}: HTTP {st}")
        return 1
    st, stacks = get(base, c["USER"], c["PASS"], f"/index.php/apps/deck/api/v1.1/boards/{a.board}/stacks")
    if st != 200:
        print(f"__DECK=error__ stacks: HTTP {st}")
        return 1
    n = sum(len(s.get("cards") or []) for s in stacks)
    print(f"__DECK=ok__ board {a.board} \"{board.get('title')}\" at {base}: {len(stacks)} stacks, {n} open cards")
    for s in sorted(stacks, key=lambda s: s.get("order", 0)):
        cards = s.get("cards") or []
        print(f"\n## {s.get('title')} ({len(cards)})")
        for cd in sorted(cards, key=lambda x: x.get("order", 0)):
            who = ",".join((u.get("participant") or {}).get("uid", "?") for u in cd.get("assignedUsers") or [])
            labels = ",".join(l.get("title", "") for l in cd.get("labels") or [])
            desc = " ".join((cd.get("description") or "").split())[:200]
            owner = (cd.get("owner") or {}).get("uid", "") if isinstance(cd.get("owner"), dict) else cd.get("owner", "")
            print(f"- #{cd.get('id')} {cd.get('title')} | by {owner} | to {who or '-'} | {labels or '-'}"
                  f" | due {when(cd.get('duedate')) or '-'} | changed {when(cd.get('lastModified'))}")
            if desc:
                print(f"    {desc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
