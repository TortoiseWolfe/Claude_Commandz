#!/usr/bin/env python3
"""Snapshots and "what changed since last run" for /extension-status (Claude_Commandz issue 2).

The command gathers the API answers it already fetches (repo counts, traffic, open issues, open PRs, external mentions) into one
JSON object and pipes it in:

    python3 extension_status_snapshot.py --repo OWNER/NAME [--since 7d] [--no-diff] < snapshot.json

The object is saved to <state>/<repo-slug>/<YYYY-MM-DD-HHMM>.json (state defaults to ~/.claude/state/extension-status, or
$EXTENSION_STATUS_STATE). The script then prints the deltas against the snapshot the run is compared with:
  - default: the newest snapshot saved before this run;
  - --since <N><m|h|d|w>: the newest snapshot at least that old;
  - --no-diff, or no such snapshot: absolute mode, with a note.
Snapshots older than 90 days are removed on every run. Nothing is synced between machines and the snapshot has no schema version:
a change in shape just makes old snapshots show odd deltas until they age out (the issue's stated trade-off).

$EXTENSION_STATUS_NOW ("YYYY-MM-DD HH:MM", local time) replaces the clock; the tests use it.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

KEEP_DAYS = 90
STAMP = "%Y-%m-%d-%H%M"


def slug(repo):
    return repo.replace("/", "-")


def parse_duration(text):
    m = re.fullmatch(r"(\d+)([mhdw])", text.strip())
    if not m:
        raise ValueError(f"not a duration like 7d, 12h, 30m or 2w: {text!r}")
    n = int(m.group(1))
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[m.group(2)]


def now():
    forced = os.environ.get("EXTENSION_STATUS_NOW")
    return datetime.strptime(forced, "%Y-%m-%d %H:%M") if forced else datetime.now().replace(second=0, microsecond=0)


def state_dir():
    return Path(os.environ.get("EXTENSION_STATUS_STATE") or Path.home() / ".claude" / "state" / "extension-status")


def snapshots(folder):
    """[(datetime, path)] oldest first; files whose names are not a stamp are ignored."""
    found = []
    for p in folder.glob("*.json"):
        try:
            found.append((datetime.strptime(p.stem, STAMP), p))
        except ValueError:
            continue
    return sorted(found)


def baseline(folder, when, since=None):
    older = [(t, p) for t, p in snapshots(folder) if t < when]
    if since is not None:
        older = [(t, p) for t, p in older if t <= when - since]
    return older[-1] if older else None


def _count(d, key):
    v = d.get(key)
    return v if isinstance(v, int) else 0


def _traffic(d, key, field):
    v = d.get(key)
    return v.get(field, 0) if isinstance(v, dict) and isinstance(v.get(field, 0), int) else 0


def _ids(d, key, field):
    items = d.get(key)
    return {str(i.get(field)): i for i in items if isinstance(i, dict)} if isinstance(items, list) else {}


def _signed(n):
    return f"+{n}" if n > 0 else str(n)


def _both(old, new, key, kind):
    """A part that one side could not fetch (a 403 on traffic, say) is left out of the comparison, not shown as a drop to zero."""
    return isinstance(old.get(key), kind) and isinstance(new.get(key), kind)


def deltas(old, new):
    lines = []
    for key, label in (("stars", "stars"), ("forks", "forks"), ("watchers", "watchers")):
        if not _both(old, new, key, int):
            continue
        d = _count(new, key) - _count(old, key)
        if d:
            lines.append(f"{_signed(d)} {label}")
    for key, label in (("clones", "clones"), ("views", "views")):
        if not _both(old, new, key, dict):
            continue
        d = _traffic(new, key, "count") - _traffic(old, key, "count")
        u = _traffic(new, key, "uniques") - _traffic(old, key, "uniques")
        if d or u:
            lines.append(f"{_signed(d)} {label} ({_signed(u)} unique)")
    for key, noun, field, short in (("issues", "issue", "number", "#"), ("prs", "PR", "number", "#"), ("mentions", "external mention", "url", "")):
        if not _both(old, new, key, list):
            continue
        before, after = _ids(old, key, field), _ids(new, key, field)
        added = [after[k] for k in after if k not in before]
        gone = [before[k] for k in before if k not in after]
        if added:
            names = ", ".join(f"{short}{i.get(field)} {i.get('title', '')}".strip() for i in added[:5])
            lines.append(f"{len(added)} new {noun}{'s' if len(added) != 1 else ''}: {names}")
        if gone and key != "mentions":
            lines.append(f"{len(gone)} {noun}{'s' if len(gone) != 1 else ''} closed or gone")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since")
    ap.add_argument("--no-diff", action="store_true")
    a = ap.parse_args(argv)
    try:
        since = parse_duration(a.since) if a.since else None
    except ValueError as e:
        print(f"extension_status_snapshot: {e}", file=sys.stderr)
        return 2
    try:
        new = json.loads(sys.stdin.read())
        if not isinstance(new, dict):
            raise ValueError("expected a JSON object")
    except ValueError as e:
        print(f"extension_status_snapshot: stdin is not a JSON object ({e})", file=sys.stderr)
        return 2

    when = now()
    folder = state_dir() / slug(a.repo)
    base = None if a.no_diff else baseline(folder, when, since)

    folder.mkdir(parents=True, exist_ok=True)
    (folder / (when.strftime(STAMP) + ".json")).write_text(json.dumps(new, indent=1, sort_keys=True))
    cutoff = when - timedelta(days=KEEP_DAYS)
    for t, p in snapshots(folder):
        if t < cutoff:
            p.unlink()

    if a.no_diff:
        print("absolute mode (--no-diff): showing the current numbers only")
    elif base is None:
        what = f"older than {a.since}" if since else "saved before this run"
        print(f"no prior snapshot {what}: absolute mode. This run is saved as the starting point.")
    else:
        t, p = base
        try:
            old = json.loads(p.read_text())
        except ValueError:
            old = {}
        print(f"since {t.strftime('%a %b %-d, %-I:%M %p')} ({p.name}):")
        lines = deltas(old, new)
        print("\n".join(f"  {l}" for l in lines) if lines else "  nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
