#!/usr/bin/env python3
"""Stop a reply that names a pull request, issue or card by number alone, with no clickable address.

Use when: Stop hook, always on; it reads the last assistant reply of the turn and blocks the stop once.

The owner reads replies in a terminal. "PR 142" tells him nothing and cannot be clicked; a markdown
link shows only its words in the terminal; an address inside an indented or fenced block is drawn as
code and is not clickable either. The standing rule: a line says what the thing is in plain words
(bold), and its full https:// address sits on the very next line by itself, unindented.

A line counts as a reference when it holds "#" plus digits (not inside a word, not "&#39;", not a
heading) or one of PR, pull request, issue, card, ticket followed by digits. Lines inside fenced
blocks and lines indented by 4 or more spaces or a tab (the technical lower layer) are skipped. A
reference line passes only when (a) a full https:// address is on that line or on one of the next
two lines, that address line being outside any fence and not indented, and (b) the line has at least
4 plain words besides the reference and markdown punctuation. Otherwise the stop is blocked once
(stop_hook_active ends the loop). The turn text comes from unverified-pending.py's turn().

Exits 0 quietly on: stop_hook_active, an unreadable transcript, any error, BARE_REFERENCE=off,
AFA_LANE_UNATTENDED=1 (nobody reads that window).

Registered on Stop: python3 ~/.claude/hooks/bare-reference.py (timeout 10)
Tests: python3 -m pytest tests/test_bare_reference.py -q -p no:cacheprovider
"""
import importlib.util
import json
import os
import re
import sys

HASH_REF = re.compile(r"(?<![\w&])#\d+(?!\w)")
WORD_REF = re.compile(r"\b(?:PR|pull request|issue|card|ticket)\s*#?\d+(?!\w)", re.I)
ADDRESS = re.compile(r"https://\S+")
FENCE = re.compile(r"^\s*(```|~~~)")
MIN_WORDS = 4
MAX_SHOWN = 4

REASON_HEAD = "This reply names {n} item(s) by number without a clickable address or without saying what they are:"
REASON_TAIL = (
    "Rewrite: a bold line that says what the thing is in plain words, then its full https:// address on the "
    "next line by itself, not indented and not in a code block. Check each address opens the right page."
)


def is_indented(line):
    return line.startswith("\t") or line.startswith("    ")


def has_reference(line):
    return bool(HASH_REF.search(line) or WORD_REF.search(line))


def plain_word_count(line):
    text = ADDRESS.sub(" ", line)
    text = HASH_REF.sub(" ", text)
    text = WORD_REF.sub(" ", text)
    return len(re.findall(r"[A-Za-z0-9][\w'’-]*", text))


def scan_reply(text):
    """Lines of the reply that name something by number without the address and words the owner needs."""
    lines = text.split("\n")
    skipped = []  # fenced or indented: the technical layer
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            skipped.append(True)
        else:
            skipped.append(in_fence or is_indented(line))
    bad = []
    for i, line in enumerate(lines):
        if skipped[i] or not has_reference(line):
            continue
        has_address = bool(ADDRESS.search(line)) or any(
            j < len(lines) and not skipped[j] and ADDRESS.search(lines[j]) for j in (i + 1, i + 2)
        )
        if not has_address or plain_word_count(line) < MIN_WORDS:
            bad.append(line.strip())
    return bad


def load_turn():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "unverified-pending.py")
    spec = importlib.util.spec_from_file_location("unverified_pending", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.turn


def main():
    try:
        if os.environ.get("BARE_REFERENCE") == "off" or os.environ.get("AFA_LANE_UNATTENDED") == "1":
            return 0
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get("stop_hook_active"):
            return 0
        transcript = payload.get("transcript_path")
        if not isinstance(transcript, str) or not os.path.isfile(transcript):
            return 0
        with open(transcript, encoding="utf-8", errors="replace") as handle:
            reply, _calls = load_turn()(handle)
        bad = scan_reply(reply)
        if not bad:
            return 0
        shown = "\n".join("  " + line[:120] for line in bad[:MAX_SHOWN])
        reason = REASON_HEAD.format(n=len(bad)) + "\n" + shown + "\n" + REASON_TAIL
        print(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
