#!/usr/bin/env python3
"""Edit cards on a Nextcloud Deck board without ever showing credentials.

    deck_write.py --env <env file> --prefix NEXTCLOUD_PROD --board <id> <action> ...

Actions:
    show     --card N                          print one card's title, stack and full description
    replace  --card N --old TEXT --new TEXT    swap TEXT in the description (must match exactly once)
    append   --card N --text LINE              add a line to the end of the description
    move     --card N --stack "Stack title"    move the card to the end of another stack
    create   --stack "Stack title" --title T [--description D] [--assign UID ...]

Credentials come from <PREFIX>_URL, <PREFIX>_USER and <PREFIX>_PASS in the env file, read inside
this process by deck_board.py's loader. They are never printed or put on a command line.
Uses the Deck REST API (index.php/apps/deck/api/v1.0); moving between stacks uses the web UI's
route, because the REST reorder call ignores the target stack. Every write reads the card back and
prints what changed, so the output is the confirmation.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deck_board import NoRedirect, read_env  # noqa: E402

API = "/index.php/apps/deck/api/v1.0"


def call(c, method, path, body=None, api=API):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(c["URL"].rstrip("/") + api + path, data=data, method=method, headers={
        "OCS-APIRequest": "true", "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{c['USER']}:{c['PASS']}".encode()).decode()})
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        sys.exit(f"deck_write: {method} {path}: HTTP {e.code}")


def find_card(c, board, card_id):
    for s in call(c, "GET", f"/boards/{board}/stacks"):
        for cd in s.get("cards") or []:
            if cd["id"] == card_id:
                return s, cd
    sys.exit(f"deck_write: card {card_id} is not open on board {board}")


def find_stack(c, board, title):
    hits = [s for s in call(c, "GET", f"/boards/{board}/stacks") if s["title"] == title]
    if len(hits) != 1:
        sys.exit(f"deck_write: {len(hits)} stacks titled {title!r} on board {board}")
    return hits[0]


def save(c, board, stack_id, cd, **changes):
    owner = cd["owner"]["uid"] if isinstance(cd.get("owner"), dict) else cd["owner"]
    body = {"title": cd["title"], "description": cd.get("description") or "", "type": cd.get("type", "plain"),
            "owner": owner, "order": cd.get("order", 0), "duedate": cd.get("duedate")}
    body.update(changes)
    return call(c, "PUT", f"/boards/{board}/stacks/{stack_id}/cards/{cd['id']}", body)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--prefix", default="NEXTCLOUD_PROD")
    ap.add_argument("--board", type=int, required=True)
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("show")
    p.add_argument("--card", type=int, required=True)
    p = sub.add_parser("replace")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p = sub.add_parser("append")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--text", required=True)
    p = sub.add_parser("move")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--stack", required=True)
    p = sub.add_parser("create")
    p.add_argument("--stack", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--assign", action="append", default=[])
    a = ap.parse_args(argv)
    c = read_env(a.env, a.prefix)
    b = a.board

    if a.action == "show":
        s, cd = find_card(c, b, a.card)
        print(f"#{cd['id']} {cd['title']} [{s['title']}]\n---\n{cd.get('description') or ''}")
    elif a.action in ("replace", "append"):
        s, cd = find_card(c, b, a.card)
        desc = cd.get("description") or ""
        if a.action == "replace":
            if desc.count(a.old) != 1:
                sys.exit(f"deck_write: --old matches {desc.count(a.old)} times in card {a.card}, need exactly 1")
            new = desc.replace(a.old, a.new)
        else:
            new = (desc.rstrip("\n") + "\n\n" if desc.strip() else "") + a.text
        save(c, b, s["id"], cd, description=new)
        _, after = find_card(c, b, a.card)
        print(f"#{a.card} description now:\n{after.get('description')}")
    elif a.action == "move":
        s, cd = find_card(c, b, a.card)
        dest = find_stack(c, b, a.stack)
        # The REST reorder route ignores stackId (200, card stays put); the web UI's own route moves it.
        call(c, "PUT", f"/cards/{cd['id']}/reorder", {"stackId": dest["id"], "order": 0}, api="/index.php/apps/deck")
        s2, _ = find_card(c, b, a.card)
        print(f"#{a.card} {cd['title']}: {s['title']} -> {s2['title']}")
        if s2["id"] != dest["id"]:
            sys.exit("deck_write: the move reported success but the card is still in the old stack")
    elif a.action == "create":
        dest = find_stack(c, b, a.stack)
        cd = call(c, "POST", f"/boards/{b}/stacks/{dest['id']}/cards",
                  {"title": a.title, "type": "plain", "order": len(dest.get("cards") or []),
                   "description": a.description})
        for uid in a.assign:
            call(c, "PUT", f"/boards/{b}/stacks/{dest['id']}/cards/{cd['id']}/assignUser", {"userId": uid})
        s2, after = find_card(c, b, cd["id"])
        who = ",".join((u.get("participant") or {}).get("uid", "?") for u in after.get("assignedUsers") or [])
        print(f"created #{after['id']} {after['title']} [{s2['title']}] to {who or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
