#!/usr/bin/env python3
"""Claude Code hook: at the moment an ask, a tool call or a finished reply matches a row of a private table, add that row's one line to the model's context or refuse the call with it.

Use when: a reminder should reach the model only at the moment it is needed (a kind of ask, a kind of command), and adding the next one should be one table row, not code or a settings change.

The engine is public; the table is private. Table: env MOMENT_LINES_TABLE, else
~/repos/hub/rules/moment-lines.tsv (missing or unreadable: the hook does nothing).
UTF-8, tab-separated, exactly 8 columns. Blank lines and lines starting with # are skipped;
a first row whose first cell is "id" is a header. A cell holding only "-" means empty.
  1 id       short unique name, [a-z0-9-]+
  2 when     "prompt", "reply", or "tool:<regex>" (the regex must fully match the tool name)
  3 who      all | hub | lane  (lane = env AFA_LANE is "1"; hub = it is not)
  4 where    regex searched (ignoring case) in cwd + newline + text; empty = anywhere
  5 pattern  regex searched (ignoring case) in the text; required
  6 unless   regex searched (ignoring case) in the text; when it matches the row does not fire
  7 action   say | once | refuse
  8 line     the one line to show (plain text, required)
The text is the prompt (first 4,000 characters); for Bash the command; for any other tool its
input as JSON (first 20,000 characters); for "reply" the last assistant reply of the turn (read
with unverified-pending.py's turn() from the same folder) with fenced code blocks and lines
indented by 4 spaces or a tab dropped, first 20,000 characters. Reply rows compile their
regexes with re.I | re.M, so ^ matches at each line start. A bad row is skipped, never an error. A refuse row with
no unless counts as once. A duplicate id keeps the first row.

Actions: say adds the line as context, once per session per row. once refuses the call the first
time it fires in a session (exit 2), then lets later matches pass; on a prompt, or with no
session_id, it behaves like say. refuse refuses every time it fires (unless is the way through);
on a prompt it behaves like say. A reply row (Stop event) of any action blocks the stop once per
session per row id: {"decision": "block", "reason": ...}, exit 0; it never fires without a
session_id or when the session memory cannot be read or written, when stop_hook_active is true,
or when env AFA_LANE_UNATTENDED is "1". At most 3 lines per event. Seen ids are kept in
<state dir>/<session_id>.json (env MOMENT_LINES_STATE, else ~/.claude/state/moment-lines);
files older than 3 days are pruned on write.

Kill switch: env MOMENT_LINES=off. Anything unexpected: exit 0 with no output (fail open).

Registered in settings.json as UserPromptSubmit, and as PreToolUse with a matcher of the tool
names the table uses (for example "Bash|Edit|Write"), timeout 5; and as Stop, timeout 10.

Check the table: python3 moment-lines.py --check [TABLE]   (prints row ids and line numbers only)
Tests: cd global/.claude/hooks && python3 -m pytest tests/test_moment_lines.py -q -p no:cacheprovider -rs
"""
import json
import os
import re
import sys
import time

DEFAULT_TABLE = "~/repos/hub/rules/moment-lines.tsv"
DEFAULT_STATE = "~/.claude/state/moment-lines"
MAX_LINES = 3
PROMPT_CHARS = 4000
TOOL_CHARS = 20000
PRUNE_DAYS = 3
PRUNE_MAX = 50
ID_RE = re.compile(r"[a-z0-9-]+")
WHOS = ("all", "hub", "lane")
ACTIONS = ("say", "once", "refuse")


def cell(value):
    value = value.strip()
    return "" if value == "-" else value


def load_table(path):
    """(rows, problems, ok). rows: dicts in table order. problems: [(line number, message)]. ok False when unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return [], [], False
    rows, problems, seen = [], [], set()
    first = True
    for number, text in enumerate(raw, 1):
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        cols = text.split("\t")
        if first:
            first = False
            if cols[0].strip() == "id":
                continue
        if len(cols) != 8:
            problems.append((number, "wrong column count"))
            continue
        rid, when, who, where, pattern, unless, action, line = [cell(c) for c in cols]
        if not ID_RE.fullmatch(rid):
            problems.append((number, "bad id"))
            continue
        if rid in seen:
            problems.append((number, "duplicate id"))
            continue
        tool_rx = None
        if when not in ("prompt", "reply"):
            if not when.startswith("tool:") or not when[5:]:
                problems.append((number, "unknown when"))
                continue
            try:
                tool_rx = re.compile(when[5:])
            except re.error:
                problems.append((number, "bad regex in when"))
                continue
        if who not in WHOS:
            problems.append((number, "unknown who"))
            continue
        if action not in ACTIONS:
            problems.append((number, "unknown action"))
            continue
        if not pattern:
            problems.append((number, "empty pattern"))
            continue
        if not line:
            problems.append((number, "empty line"))
            continue
        compiled = {}
        bad = None
        for name, rx in (("where", where), ("pattern", pattern), ("unless", unless)):
            if not rx:
                compiled[name] = None
                continue
            try:
                compiled[name] = re.compile(rx, re.I | re.M if when == "reply" else re.I)
            except re.error:
                bad = name
                break
        if bad:
            problems.append((number, "bad regex in " + bad))
            continue
        if action == "refuse" and not unless:
            problems.append((number, "refuse without unless, treated as once"))
            action = "once"
        seen.add(rid)
        rows.append({"id": rid, "tool": tool_rx, "prompt": when == "prompt", "reply": when == "reply", "who": who, "action": action,
                     "line": line, **compiled})
    return rows, problems, True


def check(argv):
    path = argv[0] if argv else os.path.expanduser(DEFAULT_TABLE)
    rows, problems, ok = load_table(path)
    if not ok:
        print("table: missing or unreadable")
        print("0 rows loaded, 1 problems")
        return 1
    for number, message in problems:
        print("line %d: %s" % (number, message))
    print("%d rows loaded, %d problems" % (len(rows), len(problems)))
    return 1 if problems else 0


def state_file(session_id):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id) if isinstance(session_id, str) else ""
    if not safe:
        return None
    folder = os.environ.get("MOMENT_LINES_STATE") or os.path.expanduser(DEFAULT_STATE)
    return os.path.join(folder, safe + ".json")


def read_shown(path, strict=False):
    """Ids already shown. strict: None when the memory exists but cannot be read (a missing file is empty)."""
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()
    except FileNotFoundError:
        return set()
    except Exception:
        return None if strict else set()


def write_shown(path, shown):
    """True when the memory was written."""
    if not path:
        return False
    try:
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(shown), f)
    except Exception:
        return False
    try:
        cutoff = time.time() - PRUNE_DAYS * 86400
        removed = 0
        for name in os.listdir(folder):
            if removed >= PRUNE_MAX:
                break
            full = os.path.join(folder, name)
            if full == path or not name.endswith(".json"):
                continue
            try:
                if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                    os.remove(full)
                    removed += 1
            except OSError:
                pass
    except Exception:
        pass
    return True


def event_text(payload, event):
    """(text, tool name) for the event, or None when there is nothing to match."""
    if event == "UserPromptSubmit":
        prompt = payload.get("prompt")
        return (prompt[:PROMPT_CHARS], None) if isinstance(prompt, str) else None
    name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(name, str) or not isinstance(tool_input, dict):
        return None
    if name == "Bash":
        command = tool_input.get("command")
        return (command, name) if isinstance(command, str) else None
    return json.dumps(tool_input, ensure_ascii=False)[:TOOL_CHARS], name


def row_fires(row, event, text, tool, cwd, lane):
    if event == "UserPromptSubmit":
        if not row["prompt"]:
            return False
    elif event == "Stop":
        if not row["reply"]:
            return False
    elif row["tool"] is None or not row["tool"].fullmatch(tool):
        return False
    if row["who"] == "lane" and not lane:
        return False
    if row["who"] == "hub" and lane:
        return False
    if row["where"] and not row["where"].search(cwd + "\n" + text):
        return False
    if not row["pattern"].search(text):
        return False
    if row["unless"] and row["unless"].search(text):
        return False
    return True


FENCE = re.compile(r"^[ ]{0,3}(```|~~~)")


def strip_code(text):
    """Drop fenced code blocks and lines indented by 4+ spaces or a tab."""
    out, fence = [], None
    for ln in text.splitlines():
        m = FENCE.match(ln)
        if fence is None and m:
            fence = m.group(1)
            continue
        if fence is not None:
            if ln.strip().startswith(fence):
                fence = None
            continue
        if ln.startswith("    ") or ln.startswith("\t"):
            continue
        out.append(ln)
    return "\n".join(out)[:TOOL_CHARS]


def last_reply(payload):
    """The last assistant reply of the turn, code removed, or None."""
    import importlib.util
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    spec = importlib.util.spec_from_file_location(
        "unverified_pending", os.path.join(os.path.dirname(os.path.abspath(__file__)), "unverified-pending.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(path, encoding="utf-8") as f:
        reply, _ = mod.turn(f)
    return strip_code(reply) if isinstance(reply, str) and reply.strip() else None


def run_stop(payload):
    if payload.get("stop_hook_active") or os.environ.get("AFA_LANE_UNATTENDED") == "1":
        return 0
    sfile = state_file(payload.get("session_id"))
    if sfile is None:
        return 0
    text = last_reply(payload)
    if not text:
        return 0
    path = os.environ.get("MOMENT_LINES_TABLE") or os.path.expanduser(DEFAULT_TABLE)
    rows, _, ok = load_table(path)
    if not ok or not rows:
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    lane = os.environ.get("AFA_LANE") == "1"
    shown = read_shown(sfile, strict=True)
    if shown is None:
        return 0
    todo = [r for r in rows if r["id"] not in shown and row_fires(r, "Stop", text, None, cwd, lane)][:MAX_LINES]
    if not todo:
        return 0
    if not write_shown(sfile, shown | {r["id"] for r in todo}):
        return 0  # memory not writable: never risk blocking twice
    lines = "\n".join("- %s (%s)" % (r["line"], r["id"]) for r in todo)
    print(json.dumps({"decision": "block", "reason": "Moment of need, before this reply stands:\n" + lines +
                      "\nRewrite the reply to cover these."}, ensure_ascii=False))
    return 0


def run():
    if os.environ.get("MOMENT_LINES", "").strip().lower() == "off":
        return 0
    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name")
    if event == "Stop":
        return run_stop(payload)
    if event not in ("UserPromptSubmit", "PreToolUse"):
        return 0
    got = event_text(payload, event)
    if got is None:
        return 0
    text, tool = got
    path = os.environ.get("MOMENT_LINES_TABLE") or os.path.expanduser(DEFAULT_TABLE)
    rows, _, ok = load_table(path)
    if not ok or not rows:
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    lane = os.environ.get("AFA_LANE") == "1"
    sfile = state_file(payload.get("session_id"))
    shown = read_shown(sfile)
    pre = event == "PreToolUse"

    refusing, saying = [], []  # (row, is_once)
    for row in rows:
        if not row_fires(row, event, text, tool, cwd, lane):
            continue
        action = row["action"]
        if not pre or (action == "once" and sfile is None):
            action = "say"
        if action == "refuse":
            refusing.append(row)
        elif action == "once":
            if row["id"] not in shown:
                refusing.append(row)
        elif row["id"] not in shown:
            saying.append(row)
    if not refusing and not saying:
        return 0

    # refusing rows first, then say rows, each group in table order
    ordered = ([(r, True) for r in refusing] + [(r, False) for r in saying])[:MAX_LINES]
    new = set(shown)
    for row, is_refusal in ordered:
        if not is_refusal or row["action"] == "once":
            new.add(row["id"])
    if new != shown:
        write_shown(sfile, new)

    if any(is_refusal for _, is_refusal in ordered):
        head = "BLOCKED by a moment line."
        if any(is_refusal and row["action"] == "once" for row, is_refusal in ordered):
            head += " This is a one-time reminder: cover it, then repeat the call."
        lines = ["- %s (%s)" % (row["line"], row["id"]) for row, _ in ordered]
        print(head + "\n" + "\n".join(lines), file=sys.stderr)
        return 2
    lines = ["- %s (%s)" % (row["line"], row["id"]) for row, _ in ordered]
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": "Moment of need:\n" + "\n".join(lines)}}, ensure_ascii=False))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        try:
            return check(sys.argv[2:3])
        except Exception:
            return 1
    try:
        return run()
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
