#!/usr/bin/env python3
"""Scoreboard for the free review panel: the counter behind "shadow until 15 clean changes, then live".

  panel_scoreboard.py add --repo R --ref REF --class own|public --panel-json FILE
                          --claude-verdict pass|revise [--claude-blocking N] [--note TEXT]
  panel_scoreboard.py status                      count, agreement, false passes, planted fault, READY / NOT READY (exit 0 / 1)
  panel_scoreboard.py planted [--fixture-dir DIR] run panel_review.py --class public on a diff with a known bug; record caught or not
  panel_scoreboard.py should-skip-claude --panel-json FILE --class C [--ref REF]     prints yes or no

The file is ~/.local/share/ledger/panel-scoreboard.jsonl (env PANEL_SCOREBOARD overrides). One JSON row per line.
--panel-json is the panel's output: either the whole stdout of panel_review.py (the __PANEL_JSON=...__ line is found) or that JSON alone.
Ready bar: >= 15 scored changes, 0 false passes, >= 90% agreement, and the latest planted-fault test caught.
Older hand-written rows (opus_first_review, no agree field) are counted too. Stdlib only."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_PATH = "~/.local/share/ledger/panel-scoreboard.jsonl"
MIN_CHANGES, MIN_AGREE = 15, 90.0
SAMPLE_ONE_IN = 4
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FIXTURE = os.path.join(HERE, "tests", "fixtures", "planted")


def board_path():
    return os.path.expanduser(os.environ.get("PANEL_SCOREBOARD") or DEFAULT_PATH)


def read_rows(path=None):
    """Every row that parses as a JSON object; a bad line is skipped, a missing file is no rows."""
    rows = []
    try:
        with open(path or board_path(), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
    except OSError:
        pass
    return rows


def append_row(row, path=None):
    p = path or board_path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n")


def load_panel(path):
    """The panel summary dict from a file holding panel_review's stdout or its JSON alone; None if unreadable."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^__PANEL_JSON=(.*)__\s*$", text, re.M)
    try:
        d = json.loads(m.group(1) if m else text)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def votes(panel):
    members = {}
    for name, v in (panel.get("experts") or {}).items():
        members[name] = v.get("verdict") if isinstance(v, dict) else v
    p = sum(1 for v in members.values() if v == "pass")
    r = sum(1 for v in members.values() if v == "revise")
    return members, p, r


def majority_of(panel, p, r):
    m = panel.get("majority")
    if m in ("pass", "revise", "split", "none"):
        return m
    return "none" if p == r == 0 else "pass" if p > r else "revise" if r > p else "split"


def scored(rows):
    """Changes that were scored against Claude (not planted tests), each normalised to (panel, claude, false_pass, agree)."""
    out = []
    for r in rows:
        if r.get("kind") == "planted":
            continue
        panel = r.get("panel_majority")
        claude = r.get("claude_verdict") or r.get("opus_first_review")
        if panel is None or claude is None:
            continue
        fp = r["false_pass"] if "false_pass" in r else (panel == "pass" and claude == "revise")
        agree = r["agree"] if "agree" in r else panel == claude
        out.append((panel, claude, bool(fp), bool(agree)))
    return out


def compute_status(rows):
    s = scored(rows)
    n = len(s)
    agree_pct = 100.0 * sum(1 for x in s if x[3]) / n if n else 0.0
    false_passes = sum(1 for x in s if x[2])
    planted = [r for r in rows if r.get("kind") == "planted"]
    caught = bool(planted[-1].get("caught")) if planted else None
    why = []
    if n < MIN_CHANGES:
        why.append(f"only {n} scored changes, need {MIN_CHANGES}")
    if false_passes:
        why.append(f"{false_passes} false pass(es), need 0")
    if n and agree_pct < MIN_AGREE:
        why.append(f"agreement {agree_pct:.0f}%, need {MIN_AGREE:.0f}%")
    if caught is None:
        why.append("no planted-fault test yet")
    elif not caught:
        why.append("latest planted-fault test was missed")
    return {"count": n, "agree_pct": agree_pct, "false_passes": false_passes, "planted": caught,
            "ready": not why, "why": why}


def cmd_add(a):
    panel = load_panel(a.panel_json)
    if panel is None:
        print("panel json unreadable", file=sys.stderr)
        return 2
    members, p, r = votes(panel)
    maj = majority_of(panel, p, r)
    blocking = a.claude_blocking if a.claude_blocking is not None else (1 if a.claude_verdict == "revise" else 0)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repo": a.repo, "ref": a.ref, "class": a.cls,
           "panel_majority": maj, "pass_votes": p, "revise_votes": r, "members": members,
           "claude_verdict": a.claude_verdict, "agree": maj == a.claude_verdict,
           "claude_blocking": blocking,
           # Any panel pass Claude revised counts, even with no blocking reasons named: the gate errs toward Claude.
           "false_pass": maj == "pass" and a.claude_verdict == "revise"}
    if a.note:
        row["note"] = a.note
    append_row(row)
    print(f"scored {a.repo} {a.ref}: panel {maj}, claude {a.claude_verdict}, {'agree' if row['agree'] else 'DISAGREE'}"
          + (", FALSE PASS" if row["false_pass"] else ""))
    return 0


def cmd_status(a):
    st = compute_status(read_rows())
    pl = {None: "none yet", True: "caught", False: "MISSED"}[st["planted"]]
    print(f"scored changes: {st['count']} (need {MIN_CHANGES})")
    print(f"agreement: {st['agree_pct']:.0f}% (need {MIN_AGREE:.0f}%)")
    print(f"false passes: {st['false_passes']} (need 0)")
    print(f"planted fault: {pl}")
    print("READY" if st["ready"] else "NOT READY: " + "; ".join(st["why"]))
    return 0 if st["ready"] else 1


def panel_cmd():
    """The command that runs panel_review.py; env PANEL_REVIEW_CMD replaces it (tests use a stub)."""
    env = os.environ.get("PANEL_REVIEW_CMD")
    return env.split() if env else [sys.executable, os.path.join(HERE, "panel_review.py")]


def cmd_planted(a):
    d = os.path.expanduser(a.fixture_dir)
    files = {k: os.path.join(d, v) for k, v in (("spec", "spec.txt"), ("acceptance", "acceptance.txt"), ("diff", "planted.diff"))}
    missing = [k for k, v in files.items() if not os.path.isfile(v)]
    if missing:
        print(f"fixture missing in {d}: {', '.join(missing)}", file=sys.stderr)
        return 2
    cmd = panel_cmd() + ["--spec", files["spec"], "--acceptance", files["acceptance"], "--diff", files["diff"],
                         "--class", "public", "--repo", a.repo]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    m = re.search(r"^__PANEL_JSON=(.*)__\s*$", out, re.M)
    try:
        panel = json.loads(m.group(1)) if m else {}
    except ValueError:
        panel = {}
    members, p, r = votes(panel)
    maj = majority_of(panel, p, r) if panel else "none"
    caught = maj == "revise"
    append_row({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "planted", "repo": a.repo, "fixture": os.path.basename(d),
                "panel_majority": maj, "pass_votes": p, "revise_votes": r, "members": members, "caught": caught})
    print(f"planted fault: {'caught' if caught else 'MISSED'} (panel {maj})")
    return 0 if caught else 1


def cmd_skip(a):
    panel = load_panel(a.panel_json)
    if panel is None or a.cls not in ("own", "public"):
        print("no")
        return 0
    if not compute_status(read_rows())["ready"]:
        print("no")
        return 0
    _, p, r = votes(panel)
    ok = panel.get("gate") == "send" and majority_of(panel, p, r) == "pass" and p >= 3 and r == 0
    key = a.ref
    if key is None:
        key = json.dumps(panel, sort_keys=True)
    audited = int(hashlib.sha256(key.encode()).hexdigest(), 16) % SAMPLE_ONE_IN == 0
    print("yes" if ok and not audited else "no")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("--repo", required=True)
    s.add_argument("--ref", required=True)
    s.add_argument("--class", dest="cls", required=True, choices=("own", "public"))
    s.add_argument("--panel-json", required=True)
    s.add_argument("--claude-verdict", required=True, choices=("pass", "revise"))
    s.add_argument("--claude-blocking", type=int, default=None)
    s.add_argument("--note", default=None)
    sub.add_parser("status")
    s = sub.add_parser("planted")
    s.add_argument("--repo-dir", "--fixture-dir", dest="fixture_dir", default=DEFAULT_FIXTURE,
                   help="folder with spec.txt, acceptance.txt, planted.diff")
    s.add_argument("--repo", default="planted-fixture")
    s.add_argument("--timeout", type=int, default=900)
    s = sub.add_parser("should-skip-claude")
    s.add_argument("--panel-json", required=True)
    s.add_argument("--class", dest="cls", required=True)
    s.add_argument("--ref", default=None)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    return {"add": cmd_add, "status": cmd_status, "planted": cmd_planted, "should-skip-claude": cmd_skip}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
