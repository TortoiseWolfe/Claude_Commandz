"""Scanner for unverified-pending.sh. Reads a transcript path (argv[1]) and prints one line per
item the LAST assistant reply hands to Jonathan without a live check THIS turn:
  - a Gmail draft he's told to send, when no Gmail lookup ran since his last message;
  - a PR he's told to merge, when no `gh pr` / pulls API call ran since his last message.
Prints nothing when clear. Self-test: `python3 unverified-pending.py --self-test`."""
import json
import re
import sys

DRAFT_ID = re.compile(r"\br-?\d{12,}\b")
SEND_CUE = re.compile(r"\b(send|to send|not sent|unsent|waiting for you|for you to|retype)\b", re.I)
PR_ASK = re.compile(r"\bmerge\b[^.\n]{0,60}?(?:[\w.-]+/)?[\w.-]*#\d+|(?:[\w.-]+/)?[\w.-]+#\d+[^.\n]{0,40}\bmerge\b", re.I)
# "Send:" as an instruction, or "for you to send", about an email/reply/draft, even without an id
SEND_ASK = re.compile(r"(^\s*(?:[-*]|\d+\.)?\s*(?:\*\*)?\s*Send\b.{0,80}\b(?:email|reply|draft|note)|"
                      r"\b(?:for you to send|still to send|left to send)\b)", re.I)
GMAIL_CHECK = re.compile(r"search_threads|list_drafts|get_draft|get_thread")
PR_CHECK = re.compile(r"\bgh\s+pr\s+(view|list|status|checks)\b|repos/[^ ]+/pulls")


def turn(lines):
    """(last assistant text, tool calls made since the last real user message)."""
    entries = []
    for ln in lines:
        try:
            entries.append(json.loads(ln))
        except Exception:
            continue
    start = 0
    for i, d in enumerate(entries):
        if d.get("type") != "user":
            continue
        c = (d.get("message") or {}).get("content")
        is_tool_result = isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
        has_text = isinstance(c, str) or (isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in c))
        if has_text and not is_tool_result:
            start = i
    last, calls = "", []
    for d in entries[start:]:
        if d.get("type") != "assistant":
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                last = b["text"]
            elif b.get("type") == "tool_use":
                calls.append(f"{b.get('name', '')} {json.dumps(b.get('input', {}))}")
    return last, calls


def scan(last, calls):
    out = []
    gmail_checked = any(GMAIL_CHECK.search(c) for c in calls)
    pr_checked = any(PR_CHECK.search(c) for c in calls)
    for ln in last.split("\n"):
        if ((DRAFT_ID.search(ln) and SEND_CUE.search(ln)) or SEND_ASK.search(ln)) and not gmail_checked:
            out.append("draft: " + ln.strip()[:120])
        elif PR_ASK.search(ln) and not pr_checked:
            out.append("PR: " + ln.strip()[:120])
    return out[:4]


def self_test():
    cases = [
        ("draft-unchecked", "fire", "1. **Send Gail's email:** draft r8399774348393883238.", []),
        ("draft-checked", "quiet", "1. **Send Gail's email:** draft r8399774348393883238.", ['mcp__claude_ai_Gmail__search_threads {"query": "in:sent"}']),
        ("pr-unchecked", "fire", "3. **Merge RescueDogs#436** (the keep-alive fix)", []),
        ("pr-checked", "quiet", "3. **Merge RescueDogs#436** (the keep-alive fix)", ['Bash {"command": "gh pr view 436 -R x/y"}']),
        ("send-no-id", "fire", "- **Send:** Gail's email, and Greg's reply after retyping its three links.", []),
        ("sent-narration", "quiet", "Gail's email went out at 4:53 PM, before I posted the roadmap.", []),
        ("narration", "quiet", "#436 merged as 1f54eec; the keep-alive run succeeded.", []),
        ("plain", "quiet", "The roadmap is updated with this afternoon's work.", []),
    ]
    fail = 0
    for name, want, text, calls in cases:
        got = scan(text, calls)
        ok = bool(got) == (want == "fire")
        fail |= not ok
        print(f"  {'ok   ' if ok else 'FAIL '} {name} -> {got}")
    print("  ALL PASS" if not fail else "  FAILURES")
    return fail


if __name__ == "__main__":
    if sys.argv[1:2] == ["--self-test"]:
        sys.exit(self_test())
    try:
        last, calls = turn(open(sys.argv[1], encoding="utf-8", errors="replace"))
    except Exception:
        sys.exit(0)
    for item in scan(last, calls):
        print(item)
