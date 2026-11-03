#!/usr/bin/env python3
"""How much of a session's context is old tool output, and what a size cap on tool results would save.

Claude Code has no setting that drops or shortens tool results from earlier turns (checked against
code.claude.com/docs, 3 Nov 2026), so this tool measures the thing a lane can act on: how much of the
conversation is tool output that is already stale, so a recycle through handoff.md pays off.

Usage:
  ctx_tool_share.py [--files LIST.txt | FILE.jsonl ...] [--old-turns N] [--caps 8000,12000,30000]
Reads Claude Code session transcripts (jsonl). Prints, per tool: calls, total characters, characters over each cap
(what the cap would send to a file instead, keeping a 2,000-character preview), and the share of the final context
(characters) that is tool output more than N assistant turns old (default 20).
Characters are about 3.5 per token for this mix; the report says tokens as characters/3.5 and says so."""

import argparse
import json
import sys
from collections import defaultdict

PREVIEW = 2000  # characters Claude Code keeps inline when output goes to a file (tools-reference.md, Output limits)
CHARS_PER_TOKEN = 3.5


def _size(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text", "")) if isinstance(b, dict) and b.get("type") == "text" else 0 for b in content)
    return 0


def scan(path):
    """-> (tool_results [(tool, chars, turn)], total_text_chars, final_turn) for one transcript."""
    names, results, other, turn = {}, [], 0, 0
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                other += len(content)
                continue
            if not isinstance(content, list):
                continue
            if msg.get("role") == "assistant":
                turn += 1
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "tool_use":
                    names[b.get("id")] = b.get("name", "?")
                    other += len(json.dumps(b.get("input", {})))
                elif t == "tool_result":
                    results.append((names.get(b.get("tool_use_id"), "?"), _size(b.get("content")), turn))
                elif t == "text":
                    other += len(b.get("text", ""))
    return results, other, turn


def tool_family(name):
    return "mcp" if name.startswith("mcp__") else name


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--files", dest="listfile")
    ap.add_argument("--old-turns", type=int, default=20)
    ap.add_argument("--caps", default="8000,12000,30000")
    a = ap.parse_args(argv)
    paths = list(a.files)
    if a.listfile:
        paths += [p.strip() for p in open(a.listfile) if p.strip()]
    caps = [int(c) for c in a.caps.split(",")]
    per = defaultdict(lambda: [0, 0] + [0] * len(caps) + [0] * len(caps))  # calls, chars, over-cap chars saved, over-cap count
    all_tool = all_other = old_tool = fin_tool = fin_other = sessions = 0
    for p in paths:
        try:
            res, other, final = scan(p)
        except OSError:
            continue
        if not res:
            continue
        sessions += 1
        tool_total = sum(c for _, c, _ in res)
        all_tool += tool_total
        all_other += other
        fin_tool += tool_total
        fin_other += other
        old_tool += sum(c for _, c, t in res if final - t > a.old_turns)
        for name, chars, _ in res:
            row = per[tool_family(name)]
            row[0] += 1
            row[1] += chars
            for i, cap in enumerate(caps):
                if chars > cap:
                    row[2 + i] += chars - PREVIEW  # characters kept out of context
                    row[2 + len(caps) + i] += 1
    if not sessions:
        print("no transcripts with tool results", file=sys.stderr)
        return 1
    tot = all_tool + all_other
    print(f"sessions {sessions}; tool-result share of conversation text {100 * all_tool / tot:.0f}%")
    print(f"tool output more than {a.old_turns} assistant turns old, at session end: {100 * old_tool / tot:.0f}% of conversation text "
          f"({100 * old_tool / max(all_tool, 1):.0f}% of all tool output)")
    print(f"{'tool':<14}{'calls':>8}{'Mchars':>9}" + "".join(f"{'>' + str(c) + ' n':>12}{'saved Mch':>11}" for c in caps))
    for name, row in sorted(per.items(), key=lambda kv: -kv[1][1])[:10]:
        line = f"{name:<14}{row[0]:>8}{row[1] / 1e6:>9.2f}"
        for i, _ in enumerate(caps):
            line += f"{row[2 + len(caps) + i]:>12}{row[2 + i] / 1e6:>11.2f}"
        print(line)
    print(f"(tokens are about characters / {CHARS_PER_TOKEN}; 'saved' = characters beyond a {PREVIEW}-character preview)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
