#!/usr/bin/env python3
"""Draft replies for /extension-status --draft-replies (Claude_Commandz issue 1).

The command pipes the open issues and pull requests it already fetched into this script:

    python3 extension_status_replies.py --owner OWNER < items.json

items.json is {"issues": [...], "prs": [...]}, each item as `gh ... --json number,title,body,author,createdAt,url,comments` gives it.
The script drops the owner's own items, sorts each remaining one into question, bug-report, feature-request, install-issue or
docs-unclear, prints a suggested reply for that class, and flags any item where the last word is an outsider's and more than 48
hours old. It only prints: no posting, commenting, closing or labelling, and no sentiment or reputation lookups.

Replies never tag anyone. A handle echoed from a title gets a zero-width character after its "@" so GitHub sends no email.
$EXTENSION_STATUS_NOW ("YYYY-MM-DD HH:MM", UTC) replaces the clock; the tests use it.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

WAIT_LIMIT = timedelta(hours=48)

# Checked in this order: the first class with a matching word wins, so an install that errors is still an install problem.
RULES = [
    ("install-issue", r"\binstall(?:s|ed|ing|ation)?\b|\bset ?up\b|\bextension add\b"),
    ("bug-report", r"\berrors?\b|\bexceptions?\b|\btraceback\b|\bcrash(?:es|ed|ing)?\b|\bbroken\b|\bnot working\b|\bdoes ?n[o']t work\b"
                   r"|\bfail(?:s|ed|ing|ure)?\b|\bbugs?\b|\bregression\b|\bstack trace\b"),
    ("feature-request", r"\bfeature request\b|\bwould be (?:nice|great)\b|\b(?:could|can) you add\b|\bplease add\b|\bsupport for\b"
                        r"|\benhancement\b|\bsuggestions?\b|\bwish\b"),
    ("docs-unclear", r"\bdocs?\b|\bdocumentation\b|\breadme\b|\bunclear\b|\bconfusing\b|\btypo\b|\bnot documented\b|\bexamples?\b"),
]

ISSUE_REPLIES = {
    "question": (
        "Thanks for asking. Here is how I would answer it, and I have linked the part of the README that covers it: <answer here>. "
        "If that does not fit what you are doing, tell me what you expected to happen and I will look again."
    ),
    "bug-report": (
        "Thanks for the report. To track this down I need the steps to reproduce it, the version of the extension and of spec-kit "
        "you are using, your operating system, and the full error text. With those I can try it on my side."
    ),
    "feature-request": (
        "Thanks for the idea. Can you tell me what you are trying to get done, and what you do today instead? That tells me "
        "whether this belongs in the extension or in your own setup."
    ),
    "install-issue": (
        "Sorry the install did not go smoothly. The install section of the README lists the exact commands, so please compare "
        "yours against it. If it still fails, send the command you ran, the version of spec-kit, and the full output."
    ),
    "docs-unclear": (
        "Thanks for pointing out where the docs lost you. Which sentence or step was unclear, and what did you expect it to say? "
        "I will fix the wording."
    ),
}

PR_REPLIES = {
    "question": "Thanks for the pull request. Can you say a little more about what it is meant to change? I will review it once I know.",
    "bug-report": (
        "Thanks for the pull request. Does it come with a test that fails without your change? That is the quickest way for me to "
        "confirm the fix."
    ),
    "feature-request": (
        "Thanks for the pull request. I will check how it fits the extension. Please describe what it adds and how you tried it."
    ),
    "install-issue": (
        "Thanks for the pull request. Please say which install step it changes and which setup you tested it on."
    ),
    "docs-unclear": "Thanks for improving the docs. I will read it through and merge it if the wording is right.",
}


def classify(title, body, is_pr=False):
    text = f"{title or ''}\n{body or ''}".lower()
    for kind, pattern in RULES:
        if re.search(pattern, text):
            return kind
    return "feature-request" if is_pr else "question"


def login(who):
    if isinstance(who, dict):
        return who.get("login") or ""
    return who or ""


def parse_time(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def now():
    forced = os.environ.get("EXTENSION_STATUS_NOW")
    if forced:
        return datetime.strptime(forced, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def safe(text):
    """Echoed text never carries a live @handle: GitHub emails whoever gets tagged."""
    return (text or "").replace("@", "@​").replace("\n", " ").strip()


def last_word(item):
    """(author, time) of the newest comment, or of the item itself when nobody commented."""
    events = [(parse_time(c.get("createdAt")), login(c.get("author"))) for c in item.get("comments") or []]
    events = [e for e in events if e[0]]
    if events:
        when, who = max(events, key=lambda e: e[0])
        return who, when
    return login(item.get("author")), parse_time(item.get("createdAt"))


def age_text(delta):
    hours = int(delta.total_seconds() // 3600)
    return f"{hours // 24}d" if hours >= 72 else f"{hours}h"


def build(data, owner, clock):
    rows = []
    for key, is_pr in (("issues", False), ("prs", True)):
        for item in data.get(key) or []:
            if login(item.get("author")).lower() == owner.lower():
                continue
            kind = classify(item.get("title"), item.get("body"), is_pr)
            who, when = last_word(item)
            waiting = None
            replied = False
            if when is not None:
                if who.lower() == owner.lower():
                    replied = True
                elif clock - when > WAIT_LIMIT:
                    waiting = clock - when
            rows.append({
                "number": item.get("number"), "title": safe(item.get("title")), "is_pr": is_pr, "kind": kind,
                "url": item.get("url") or "", "waiting": waiting, "replied": replied,
                "opened": parse_time(item.get("createdAt")) or clock,
            })
    # Oldest first, as the triage table is.
    rows.sort(key=lambda r: r["opened"])
    return rows


def render(rows):
    if not rows:
        return "✓ No inbound items to draft replies for"
    waiting = sum(1 for r in rows if r["waiting"] is not None)
    noun = "item" if len(rows) == 1 else "items"
    out = [f"💬 Draft replies: {len(rows)} outside {noun}, {waiting} waiting over 48h", ""]
    for r in rows:
        label = "PR" if r["is_pr"] else "issue"
        out.append(f"#{r['number']} [{r['kind']}] {r['title']} ({label}) {r['url']}".rstrip())
        if r["waiting"] is not None:
            out.append(f"  ⚠ waiting {age_text(r['waiting'])} for an owner reply")
        elif r["replied"]:
            out.append("  ✓ owner replied last")
        out.append("  💬 Suggested reply")
        out.append("  > " + (PR_REPLIES if r["is_pr"] else ISSUE_REPLIES)[r["kind"]])
        out.append("")
    out.append("Nothing is posted: copy a reply if it fits, and change the part in angle brackets.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--owner", required=True, help="author login to leave out (the repo owner)")
    args = ap.parse_args(argv)
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("expected an object with issues and prs")
    except ValueError as e:
        print(f"extension_status_replies: input is not the expected JSON ({e})", file=sys.stderr)
        return 2
    print(render(build(data, args.owner, now())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
