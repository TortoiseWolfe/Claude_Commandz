#!/usr/bin/env python3
"""Where is this lane, and does it need me? One command instead of lane.sh --status, tmux capture-pane, report.md and lane.sh --recycle-point.

usage: lane_brief.py <lane> [--lines N] [--json]
  exit 0 = printed the brief, 2 = no lane by that name in index.tsv (or a bad option).

It reads and never writes: no keys are sent to a pane, no file is changed (not even the index, which lane.sh --ctx can rewrite when it
follows a lane to a new session). The rules are copied from the scripts that already decide these things, so the answers agree:
  report verdict   lane_report_state.py, used the way lane.sh --status (report_word) uses it
  context          the last assistant usage in the lane's transcript, as lane.sh ctx_k reads it; the session is the one lane.sh
                   follow_session would pick (the lane's SessionStart hook record), looked up here without writing it back
  recycle line     lane.sh --recycle-point <lane>
  held dialog      lane_watch.sh dialog_waiting: the last 15 lines of the visible screen match DIALOG_WAITING below
  working / idle   lane.sh --status: a spinner line on screen is working, "Credit balance too low" / "API Error" is error, else idle
  idle suggestion  lane.sh pane_input_text: grey (SGR 2) text on the input line is a suggestion that Enter would not send
  headless lanes   lane.sh --status: out.log ends with "exit <code>" when the process is done; the engine's blocked-until comes from
                   the dispatcher's openclaw-dispatch.json (<engine>_blocked_until)

Text is for the hub's eyes: long lines are joined (tmux capture-pane -J), never cut at the screen width. --json prints the same facts
as one JSON object with nothing cut."""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # python3 -I (how lane.sh runs its helpers) leaves the script folder off the path
from lane_handoff_summary import handoff_summary  # noqa: E402
from lane_report_state import report_state  # noqa: E402

SESSION = "lanes"
HEADLESS_ENGINES = ("codex", "copilot", "agy", "opencode")
# lane_watch.sh dialog_waiting: Claude Code menus say "Enter to select"; permission prompts say "Do you want to proceed" and end with
# "Esc to cancel". Only the last 15 lines of the visible screen are checked. test_lane_brief.py fails if lane_watch.sh's pattern changes.
DIALOG_WAITING = r"Enter to select|Do you want to proceed|Esc to cancel"
DIALOG_TAIL = 15
RULE_LINE = re.compile(r"^\s*[─━═]+\s*$")  # lane.sh --status skips the same horizontal rules
CHROME_LINE = re.compile(r"⏵|🐢")  # the footer mode line and the mods band, skipped by lane.sh --status for the same reason
LAUNCH_LINE = re.compile(r"AFA_LANE(_NAME)?=|console_worker\.sh --inner|^\s*\$ (cd |claude )")  # the command lane.sh typed to start the lane
SPINNER = re.compile(r"…\s*\(\d+(m \d+)?s")
ERROR_LINE = re.compile(r"Credit balance too low|API Error")
ALIVE_COMMANDS = ("claude", "node")
VERSION_LIKE = re.compile(r"^v?\d+(\.\d+)+")  # a Claude Code build shows up as the pane command, e.g. 2.1.119
DEAD_COMMANDS = ("bash", "zsh", "sh", "dash", "fish", "")
MAX_TEXT_LINE = 1200  # text view only: one line past this is cut with a note saying how much; --json is never cut
SUGGESTION_LABEL = "(suggestion, not typed)"
EASTERN = ZoneInfo("America/New_York")


def state_dir():
    return Path.home() / ".claude" / "state"


def clock12(ts, today=True):
    """8:01 AM for a time today; 'Tue Oct 27 8:01 AM' for another day."""
    d = datetime.fromtimestamp(ts)
    if today and d.date() == datetime.now().date():
        return d.strftime("%-I:%M %p")
    return d.strftime("%a %b %-d %-I:%M %p")


def eastern12(ts):
    return datetime.fromtimestamp(ts, EASTERN).strftime("%-I:%M %p %a %b %-d %Z")


# ---- the lane's index row -------------------------------------------------------------------------------------------------------

FIELDS = ("date", "name", "dir", "model", "billing", "prompt", "report", "session", "start", "effort", "connectors", "recycle_at",
          "high_tier", "project_mcp", "add_dirs")


def read_index(path=None):
    path = path or state_dir() / "lanes" / "index.tsv"
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            parts += ["-"] * (len(FIELDS) - len(parts))
            rows.append(dict(zip(FIELDS, parts)))
    return rows


def lane_row(rows, name):
    """The newest row for the name (lane.sh index_row: the last one wins)."""
    found = None
    for r in rows:
        if r["name"] == name:
            found = r
    return found


def lane_folder(row, name):
    """The lane's hand-off folder: the one its report path sits in, else the newest *-<name> folder."""
    rep = row.get("report", "-")
    if rep not in ("", "-") and Path(rep).parent.is_dir():
        return Path(rep).parent
    folders = sorted(Path(state_dir() / "lanes").glob("*-" + name), key=lambda p: p.stat().st_mtime, reverse=True)
    return folders[0] if folders else (Path(rep).parent if rep not in ("", "-") else None)


# ---- tmux (read-only: display and capture-pane only) ------------------------------------------------------------------------------

def tmux(*args):
    """-> stdout of tmux <args>, or None when tmux is missing, fails (no such window) or hangs."""
    try:
        r = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def pane_command(name):
    out = tmux("display", "-p", "-t", f"{SESSION}:{name}", "#{pane_current_command}")
    return None if out is None else out.strip()


def pane_alive(cmd):
    """True (running), False (dead) or None (a program this does not know)."""
    if cmd is None:
        return False
    c = cmd.strip()
    if c in ALIVE_COMMANDS or VERSION_LIKE.match(c):
        return True
    if c in DEAD_COMMANDS:
        return False
    return None


def dialog_held(screen):
    tail = "\n".join((screen or "").splitlines()[-DIALOG_TAIL:])
    return re.search(DIALOG_WAITING, tail) is not None


def dialog_lines(joined, limit=15):
    """The dialog's first lines: from the horizontal rule above its last trigger line (its top border) down, joined lines, blanks dropped."""
    lines = [ln.rstrip() for ln in (joined or "").splitlines()]
    hit = None
    for i in range(len(lines) - 1, -1, -1):
        if re.search(DIALOG_WAITING, lines[i]):
            hit = i
            break
    if hit is None:
        return []
    top = max(0, hit - 40)
    for i in range(hit, top - 1, -1):
        if RULE_LINE.match(lines[i]):
            top = i + 1
            break
    return [ln for ln in lines[top:] if ln.strip() and not RULE_LINE.match(ln)][:limit]


SGR = re.compile(r"\x1b\[([0-9;:]*)m")
CSI = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
ANSI = CSI  # colour codes in a headless engine's out.log


def input_text(pane_e):
    """-> (typed, dim) text on the lane's input line (the last line that starts with the prompt arrow) from a capture-pane -e pane.
    Copied from lane.sh pane_input_text: 'typed' is the normal-colour text, 'dim' is Claude Code's grey suggestion (SGR 2)."""
    dim, found = False, None
    for line in (pane_e or "").split("\n"):
        out = {"typed": "", "dim": ""}
        arrow, i = None, 0
        while i < len(line):
            m = SGR.match(line, i)
            if m:
                ps = m.group(1).replace(":", ";").split(";")
                j = 0
                while j < len(ps):
                    p = ps[j] or "0"
                    if p in ("38", "48", "58"):
                        j += 3 if j + 1 < len(ps) and ps[j + 1] == "5" else 5
                        continue
                    if p == "0":
                        dim = False
                    elif p == "2":
                        dim = True
                    elif p == "22":
                        dim = False
                    j += 1
                i = m.end()
                continue
            m = CSI.match(line, i)
            if m:
                i = m.end()
                continue
            c = line[i]
            i += 1
            if arrow is None:
                if c == "❯":
                    arrow = True
                elif not c.isspace():
                    arrow = False
                continue
            if arrow is False:
                continue
            out["dim" if dim else "typed"] += c
        if arrow:
            found = out
    return (found["typed"].strip(), found["dim"].strip()) if found else ("", "")


def screen_tail(joined, n):
    """The last n non-blank lines of a joined capture, without the launch command, horizontal rules or footer chrome."""
    keep = []
    for ln in (joined or "").splitlines():
        ln = ln.rstrip()
        if not ln.strip() or RULE_LINE.match(ln) or CHROME_LINE.search(ln) or LAUNCH_LINE.search(ln):
            continue
        keep.append(ln)
    return keep[-n:] if n > 0 else []


def label_suggestion(lines, typed, dim):
    """Mark the input-box line as a suggestion when its text is grey and nothing is typed. -> (lines, suggestion text or '')."""
    if typed or not dim:
        return lines, ""
    out = list(lines)
    for i in range(len(out) - 1, -1, -1):
        s = out[i].lstrip()
        if s.startswith("❯") and dim[:20] in s:
            out[i] = out[i] + "  " + SUGGESTION_LABEL
            return out, dim
    return out, dim


# ---- files ------------------------------------------------------------------------------------------------------------------------

def tail_lines(path, n, chunk=65536):
    """The last n lines of a file without reading all of it."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos, data = f.tell(), b""
            while pos > 0 and data.count(b"\n") <= n:
                step = min(chunk, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return None
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def proven_session(name, start, rows):
    """lane.sh proven_session, read-only: the session id the lane's own SessionStart hook recorded, when it is at/after the lane's start,
    its pane (when tmux can say) is a window of that name, and no other lane's row claims it. '' when there is none."""
    f = state_dir() / "lanes" / "sessions" / name
    try:
        last = f.read_text(errors="replace").strip().splitlines()[-1]
    except (OSError, IndexError):
        return ""
    parts = last.split("\t")
    sid, ts, pane = (parts + ["", "", ""])[:3]
    try:
        started = int(start)
    except ValueError:
        started = 0
    if not ts.isdigit() or int(ts) < started - 2:
        return ""
    if pane.startswith("%"):
        win = tmux("display", "-p", "-t", pane, "#{window_name}")
        if win and win.strip() and win.strip() != name:
            return ""
    if any(r["name"] != name and r["session"] == sid for r in rows):
        return ""
    return sid


def live_session(row, rows):
    """lane.sh --ctx's choice of session, without the write-back: the hook's record when its transcript exists, else the row's id."""
    sid = row["session"]
    if sid in ("", "-", "?"):
        sid = proven_session(row["name"], row["start"], rows)
        return sid
    best = proven_session(row["name"], row["start"], rows)
    proj = Path.home() / ".claude" / "projects" / slug(row["dir"])
    if best and best != sid and (proj / f"{best}.jsonl").exists():
        return best
    return sid


def transcript_path(row, sid):
    if not sid or sid in ("-", "?"):
        return None
    return Path.home() / ".claude" / "projects" / slug(row["dir"]) / f"{sid}.jsonl"


def context_k(transcript):
    """Context in K tokens from the last assistant usage in the last 400 lines of the transcript (lane.sh ctx_k); None if unreadable."""
    lines = tail_lines(transcript, 400) if transcript else None
    if not lines:
        return None
    last = None
    for line in lines:
        try:
            o = json.loads(line)
        except ValueError:
            continue
        m = o.get("message")
        u = m.get("usage") if isinstance(m, dict) and o.get("type") == "assistant" else None
        if u:
            last = u
    if last is None:
        return None
    n = sum(last.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return round(n / 1000)


def lane_sh():
    env = os.environ.get("LANE_SH")
    if env:
        return env
    here = HERE / "lane.sh"
    return str(here if here.exists() else Path.home() / ".claude" / "scripts" / "lane.sh")


def recycle_point(name):
    try:
        r = subprocess.run([lane_sh(), "--recycle-point", name], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else None


def report_verdict(report, transcript):
    """lane.sh report_word: not yet (no report or an empty one), stale (the lane got input or started a job after it), else written."""
    try:
        has = Path(report).stat().st_size > 0
    except OSError:
        has = False
    if not has:
        return "not yet", ""
    if transcript and os.access(transcript, os.R_OK) and Path(transcript).is_file():
        st = report_state(str(report), str(transcript))
        if st in ("input", "job"):
            return "stale", st
    return "written", ""


STALE_WHY = {
    "input": "the lane got input after report.md was written; the report is not its last word",
    "job": "a background job it started has not finished",
}


def first_line(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    for ln in lines[:1] + [x for x in lines[1:] if x.strip()][:1]:
        if ln.strip():
            return ln.strip()
    return ""


def blocked_until(engine):
    try:
        state = json.loads((state_dir() / "openclaw-dispatch.json").read_text())
    except (OSError, ValueError):
        return None
    v = state.get(engine + "_blocked_until") if isinstance(state, dict) else None
    return float(v) if isinstance(v, (int, float)) else None


# ---- the brief --------------------------------------------------------------------------------------------------------------------

def brief(name, n_lines=12, now=None):
    rows = read_index()
    row = lane_row(rows, name)
    if row is None:
        return None
    now = now if now is not None else time.time()
    folder = lane_folder(row, name)
    billing = row["billing"]
    engine = billing if billing in HEADLESS_ENGINES else "claude"
    out_log = folder / "out.log" if folder else None
    headless = engine != "claude" or bool(out_log and out_log.exists())
    b = {"lane": name, "folder": str(folder) if folder else None, "engine": engine, "billing": billing, "model": row["model"],
         "effort": row["effort"], "connectors": row["connectors"], "dir": row["dir"], "headless": headless}

    # transcript: context and the report verdict both read it
    sid = live_session(row, rows) if engine == "claude" else ""
    transcript = transcript_path(row, sid)
    b["session"] = sid or None

    report = folder / "report.md" if folder else Path(row["report"])
    verdict, why = report_verdict(report, transcript)
    rep = {"path": str(report), "state": verdict, "why": why, "line1": first_line(report), "time": None, "time12": None}
    try:
        mt = report.stat().st_mtime
        rep["time"], rep["time12"] = mt, clock12(mt)
    except OSError:
        mt = None
    b["report"] = rep

    hand = folder / "handoff.md" if folder else None
    h = {"path": str(hand) if hand else None, "exists": bool(hand and hand.exists()), "time12": None, "newer_than_report": False, "summary": ""}
    if h["exists"]:
        hm = hand.stat().st_mtime
        h["time12"] = clock12(hm)
        h["newer_than_report"] = mt is None or hm > mt
        if h["newer_than_report"]:
            h["summary"] = handoff_summary(hand)
    b["handoff"] = h

    ctx = context_k(transcript) if engine == "claude" else None
    b["context_k"] = ctx
    b["recycle_k"] = recycle_point(name) if engine == "claude" else None

    if headless:
        b["pane"] = None
        b["dialog"] = {"held": False, "lines": []}
        tail = tail_lines(out_log, 400) if out_log else None
        tail = [ANSI.sub("", ln).rstrip() for ln in (tail or []) if ANSI.sub("", ln).strip()]
        code = ""
        if tail:
            m = re.match(r"^exit (\d+)$", tail[-1])
            code = m.group(1) if m else ""
        done = bool(tail) and code != ""
        b["status"] = ("done" if code == "0" else f"exit{code}") if done else "working"
        b["out_log"] = {"path": str(out_log) if out_log else None, "lines": tail[-n_lines:] if n_lines > 0 else []}
        b["tail"] = None
        bu = blocked_until(engine) if engine in HEADLESS_ENGINES else None
        b["blocked"] = {"engine": engine, "until": bu, "until_eastern": eastern12(bu) if bu else None, "blocked": bool(bu and bu > now)}
        return b

    cmd = pane_command(name)
    alive = pane_alive(cmd)
    b["pane"] = {"command": cmd, "alive": alive}
    screen = tmux("capture-pane", "-p", "-t", f"{SESSION}:{name}")
    joined = tmux("capture-pane", "-p", "-J", "-S", "-200", "-t", f"{SESSION}:{name}")
    pane_e = tmux("capture-pane", "-e", "-p", "-t", f"{SESSION}:{name}")
    held = alive is not False and dialog_held(screen)
    b["dialog"] = {"held": held, "lines": dialog_lines(joined) if held else []}
    if joined is None and screen is None:
        b["status"] = "no window"
    elif screen and ERROR_LINE.search(screen):
        b["status"] = "error"
    elif screen and SPINNER.search(screen):
        b["status"] = "working"
    else:
        b["status"] = "idle"
    typed, dim = input_text(pane_e) if alive is not False else ("", "")
    lines, sug = label_suggestion(screen_tail(joined, n_lines), typed, dim)
    b["tail"] = {"lines": lines, "suggestion": sug or None}
    return b


def cut(text):
    if len(text) <= MAX_TEXT_LINE:
        return text
    return text[:MAX_TEXT_LINE] + f" ...[+{len(text) - MAX_TEXT_LINE} more characters; --json has them]"


def headline(b):
    """One line: does this lane need the hub?"""
    p = b["pane"]
    if b["headless"]:
        blk = b["blocked"]
        extra = f"; {b['engine']} is BLOCKED until {blk['until_eastern']}" if blk["blocked"] else ""
        return f"headless {b['engine']} lane, {b['status']}{extra}"
    if p["alive"] is False:
        return "DEAD: the claude process is gone (resume with lane.sh --resume %s)" % b["lane"]
    if b["dialog"]["held"]:
        return "NEEDS YOU: waiting on a dialog"
    if b["recycle_k"] and b["context_k"] and b["context_k"] >= b["recycle_k"]:
        return f"{b['status']}, past its recycle line ({b['context_k']}K of {b['recycle_k']}K)"
    r = b["report"]["state"]
    word = {"written": "report written", "stale": "report stale", "not yet": "no report yet"}[r]
    return f"{b['status']}, {word}"


def render(b, n_lines):
    o = []
    add = o.append
    add(f"lane {b['lane']}: {headline(b)}")
    add(f"folder: {b['folder']}")
    r = b["report"]
    if r["state"] == "not yet":
        add("report: not yet (no report.md, or it is empty)")
    else:
        word = "report written" if r["state"] == "written" else f"report stale ({r['why']})"
        add(f"report: {word}, file time {r['time12']}")
        if r["state"] == "stale":
            add(f"  stale because {STALE_WHY[r['why']]}")
        add("  line 1: " + cut(r["line1"] or "(blank)"))
    h = b["handoff"]
    if h["exists"]:
        add(f"hand-off: handoff.md at {h['time12']}" + (" (newer than the report)" if h["newer_than_report"] else ""))
        if h["summary"]:
            add("  " + cut(h["summary"]))
    else:
        add("hand-off: none")
    eng = b["engine"] if b["engine"] != "claude" else f"claude ({b['billing']})"
    extra = "" if b["headless"] and b["engine"] != "claude" else f"  effort: {b['effort']}  connectors: {b['connectors']}"
    add(f"engine: {eng}  model: {b['model']}{extra}")
    if b["engine"] == "claude":
        ck, rk = b["context_k"], b["recycle_k"]
        c = f"{ck}K" if ck is not None else "?"
        line = f"context: {c} of recycle line {rk if rk is not None else '?'}K"
        if ck is not None and rk:
            line += f" ({round(100 * ck / rk)}%)" + ("  OVER THE LINE" if ck >= rk else "")
        add(line)
    if b["headless"]:
        blk = b["blocked"]
        if blk["engine"] in HEADLESS_ENGINES:
            if blk["blocked"]:
                add(f"quota: {blk['engine']} BLOCKED until {blk['until_eastern']} (Eastern)")
            else:
                add(f"quota: {blk['engine']} not blocked" + (f" (last block ended {blk['until_eastern']})" if blk["until"] else ""))
        add(f"state: {b['status']}")
        ol = b["out_log"]
        add(f"out.log, last {len(ol['lines'])} non-blank lines ({ol['path']}):")
        o.extend("  " + cut(ln) for ln in ol["lines"]) if ol["lines"] else add("  (empty or missing)")
        return "\n".join(o)
    p = b["pane"]
    if p["alive"] is False:
        add("pane: DEAD (pane shows %s)" % (p["command"] if p["command"] else "nothing: no window named %s in tmux session %s" % (b["lane"], SESSION)))
    elif p["alive"] is None:
        add(f"pane: running {p['command']} (not a program lane_brief knows; check it)")
    else:
        add(f"pane: running {p['command']}  state: {b['status']}")
    d = b["dialog"]
    if d["held"]:
        add("dialog: YES, the lane is waiting on a dialog. It starts:")
        o.extend("  | " + cut(ln) for ln in d["lines"])
    else:
        add("dialog: no")
    t = b["tail"]
    add(f"screen, last {len(t['lines'])} non-blank lines:")
    o.extend("  " + cut(ln) for ln in t["lines"]) if t["lines"] else add("  (nothing readable: the window is gone or empty)")
    return "\n".join(o)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lane_brief.py", description="One-screen status of a lane. Read-only.")
    ap.add_argument("lane")
    ap.add_argument("--lines", type=int, default=12, metavar="N", help="screen lines to show (default 12)")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    a = ap.parse_args(argv)
    b = brief(a.lane, a.lines)
    if b is None:
        names = sorted({r["name"] for r in read_index()})
        near = difflib.get_close_matches(a.lane, names, n=3, cutoff=0.5)
        print(f"unknown lane: {a.lane} (no row in {state_dir() / 'lanes' / 'index.tsv'})" + (f"; did you mean: {', '.join(near)}" if near else ""), file=sys.stderr)
        return 2
    print(json.dumps(b, ensure_ascii=False) if a.json else render(b, a.lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
