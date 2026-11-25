#!/usr/bin/env python3
"""Claude Code PreToolUse hook (AskUserQuestion): refuse a decision card that breaks the card rules.

Refuses a question dialog (a "decision card") unless it holds one question, has a Hold option
first, names any numbered reference in plain words with its full link, and, when it speaks of
mail or pull-request state, follows a live check made since the owner's last message.
Use when: a session is about to put a decision card in front of the owner.

Why: the owner came back to a waiting dialog and the click that only focused the window picked
option 1. A batch of decisions in one message was overwhelming. And cards asked about items he
had already sent or merged, because nobody looked at the live state first.

stdin: {"tool_name": "AskUserQuestion", "tool_input": {"questions": [...]}, "transcript_path": ...}
block: every failed check in one message on stderr, exit 2.  allow: exit 0.

Checks (all failures are collected, so one retry fixes everything):
  1. exactly one question per card;
  2. option 1 starts with "Hold" (e.g. "Hold, ask me again") and is not marked (Recommended);
  3. a "#123", PR, pull request, issue, card or ticket number in the card needs an https://
     address somewhere in the card and a question of at least 12 words besides the reference;
  4. mail words (send, sent, unsent, draft) need a Gmail lookup, and pull-request words (merge,
     pull request, PR 12) need a gh pr call, among the tool calls made since the owner's last
     message. Reads the transcript with unverified-pending.py from the same folder; if the
     transcript or that file cannot be read, only this check is skipped.

Kill switch: ASK_CARD_GUARD=off in the environment.
Other tools, unreadable stdin and any unexpected error: allow, so a bug here cannot stop every card.
Tests: cd global/.claude/hooks && python3 -m pytest tests/test_ask_card_guard.py -q -p no:cacheprovider
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

MIN_WORDS = 12

# A numbered pointer: #123 (not inside a word or an HTML entity such as &#39;), or a kind word and a number.
HASH_REF = re.compile(r"(?<![\w&])#\d+")
WORD_REF = re.compile(r"(?<!\w)(?:PR|pull\s+request|issue|card|ticket)s?\s*#?\s*\d+", re.I)
MAIL_CUE = re.compile(r"\b(?:send|sent|unsent|draft|drafts)\b", re.I)
PR_CUE = re.compile(r"\b(?:merge|merged|merges)\b|\bpull\s+request\b|\bPR\s*#?\s*\d+", re.I)
URL = re.compile(r"https://\S")


def load_scanner():
    """unverified-pending.py by path (its name has a hyphen), or None when it cannot be loaded."""
    try:
        path = Path(__file__).resolve().parent / "unverified-pending.py"
        spec = importlib.util.spec_from_file_location("unverified_pending", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def card_parts(payload):
    """(questions as a list, first question dict or {}, card text)."""
    tool_input = payload.get("tool_input")
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list):
        questions = []
    texts = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        texts.append(str(q.get("question") or ""))
        opts = q.get("options")
        for o in opts if isinstance(opts, list) else []:
            if isinstance(o, dict):
                texts.append(str(o.get("label") or ""))
                texts.append(str(o.get("description") or ""))
    first = questions[0] if questions and isinstance(questions[0], dict) else {}
    return questions, first, "\n".join(texts)


def check_one_question(questions):
    if len(questions) == 1:
        return []
    return ["One decision per card: this call holds %d questions. Ask the most time-bound one now; "
            "the rest wait on the to-do list." % len(questions)]


def check_hold_first(first):
    opts = first.get("options")
    label = ""
    if isinstance(opts, list) and opts and isinstance(opts[0], dict):
        label = str(opts[0].get("label") or "").strip()
    fix = ("Option 1 must be 'Hold, ask me again' (a focus click or Enter picks option 1). "
           "Put the recommended choice second, marked (Recommended).")
    if not label.lower().startswith("hold"):
        return [fix]
    if "(recommended)" in label.lower():
        return ["Option 1 is marked (Recommended). " + fix]
    return []


def references(text):
    return [m.group(0) for m in HASH_REF.finditer(text)] + [m.group(0) for m in WORD_REF.finditer(text)]


def check_reference(first, text):
    refs = references(text)
    if not refs:
        return []
    out = []
    name = refs[0].strip()
    if not URL.search(text):
        out.append("The card names %s by number but carries no link. Say what it is in plain words (its own "
                   "title, what yes and no each do) and put its full https:// address in an option description." % name)
    question = str(first.get("question") or "")
    bare = WORD_REF.sub(" ", HASH_REF.sub(" ", question))
    words = len(bare.split())
    if words < MIN_WORDS:
        out.append("The question names %s but has only %d words besides the reference (needs %d). Say in plain "
                   "words what it is, its title and what yes and no each do, instead of pointing at the number."
                   % (name, words, MIN_WORDS))
    return out


def check_live_state(payload, text):
    mail = bool(MAIL_CUE.search(text))
    pull = bool(PR_CUE.search(text))
    if not (mail or pull):
        return []
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return []
    scanner = load_scanner()
    if scanner is None:
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            _, calls = scanner.turn(f)
        out = []
        if mail and not any(scanner.gmail_check(c) for c in calls):
            out.append("The card asks about a draft or sent mail, and no Gmail lookup ran since the owner's last "
                       "message. Check it live first (is it already sent?), drop what is done, then ask.")
        # PR_CHECK is the gh pr / pulls-API pattern unverified-pending.py's scan() uses.
        if pull and not any(scanner.PR_CHECK.search(c) for c in calls):
            out.append("The card asks about a pull request, and no gh pr call ran since the owner's last message. "
                       "Check it live first (gh pr view <n> --json state), drop what is done, then ask.")
        return out
    except Exception:
        return []


def main():
    if os.environ.get("ASK_CARD_GUARD", "").strip().lower() == "off":
        return 0
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict) or payload.get("tool_name") != "AskUserQuestion":
            return 0
        questions, first, text = card_parts(payload)
        problems = (check_one_question(questions) + check_hold_first(first)
                    + check_reference(first, text) + check_live_state(payload, text))
    except Exception:
        return 0
    if problems:
        print("BLOCKED: this decision card breaks the card rules. Fix all of these and ask again:\n"
              + "\n".join("- " + p for p in problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
