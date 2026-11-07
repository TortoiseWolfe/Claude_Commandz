#!/usr/bin/env python3
"""Ping once for each at-risk item on the hub board. No model, no network of its own.

It runs `notes_ledger.py risk`, reads the AT-RISK lines, and sends one tray notification
(`openclaw_tray.py notify`) for each item id it has not pinged before. The ids already
pinged live in a state file, one per line, so a second run says nothing new.

  risk_ping.py                       ping new at-risk items
  risk_ping.py --dry-run             print what would be pinged; change nothing
  risk_ping.py --state F             state file (default ~/.claude/state/risk-pinged.txt)
  risk_ping.py --no-net              pass --no-net to notes_ledger.py (no GitHub lookups)
  risk_ping.py --ledger CMD          replace the risk command (tests)
  risk_ping.py --notify CMD          replace the notify command (tests); title and body are appended

Exit codes: 0 = ran (pinged or nothing to ping); 1 = the ledger or a notification failed
(stderr says which). A failed ledger never reads as "nothing at risk", and an id is saved
only after its notification went out, so a failed ping is tried again on the next run.
This script is not wired into any timer.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("HOME", str(Path.home())))
DEFAULT_STATE = HOME / ".claude/state/risk-pinged.txt"
LINE = re.compile(r"^AT-RISK (\S+) (.*)$")


def read_state(path):
    try:
        return {l.strip() for l in path.read_text().splitlines() if l.strip()}
    except FileNotFoundError:
        return set()


def at_risk(ledger_cmd):
    r = subprocess.run(ledger_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write("risk_ping: ledger command failed (%d): %s\n" % (r.returncode, r.stderr.strip()[:300]))
        return None
    found = []
    for line in r.stdout.splitlines():
        m = LINE.match(line)
        if m:
            found.append((m.group(1), line))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--no-net", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger")
    ap.add_argument("--notify")
    a = ap.parse_args(argv)

    ledger = shlex.split(a.ledger) if a.ledger else [sys.executable, str(HERE / "notes_ledger.py"), "risk"] + (["--no-net"] if a.no_net else [])
    notify = shlex.split(a.notify) if a.notify else [sys.executable, str(HERE / "openclaw_tray.py"), "notify"]
    state = Path(a.state)

    items = at_risk(ledger)
    if items is None:
        return 1
    seen = read_state(state)
    failed = 0
    for item_id, line in items:
        if item_id in seen:
            continue
        title = "At risk: %s" % item_id
        if a.dry_run:
            print("would ping: %s" % line)
            continue
        r = subprocess.run(notify + [title, line], capture_output=True, text=True)
        if r.returncode != 0:
            failed += 1
            sys.stderr.write("risk_ping: notify failed for %s (%d): %s\n" % (item_id, r.returncode, r.stderr.strip()[:300]))
            continue
        state.parent.mkdir(parents=True, exist_ok=True)
        with state.open("a") as f:
            f.write(item_id + "\n")
        seen.add(item_id)
        print("pinged: %s" % item_id)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
