#!/usr/bin/env python3
"""The hub board's ledger: a state word for every open item, a stale view, work orders and an at-risk check. No model, no network
unless a line names a GitHub pull request or commit (and then only `gh`, with --no-net to turn that off).

  notes_ledger.py counts [--stale-days 10]           lines by owner, state and age, characters and a token estimate; never prints line text
  notes_ledger.py stale  [--stale-days 10]          the open items older than that with no change since (the default `open` read hides them)
  notes_ledger.py ledger [--owner P] [--state S,S] [--write FILE]  one line per fresh open item: id, state, due, evidence
  notes_ledger.py order  [--match S] [--owner P] [--state S,S] [--include-stale] [--limit N]
                                                     ONE block per open item: state, the decision already made, the deadline, the evidence
  notes_ledger.py risk                               one escalation line per at-risk item (empty output = nothing at risk)

Common flags: --inbox F  --lanes-dir D  --now "YYYY-MM-DD HH:MM" (tests)  --no-net

A state is set by this script from facts it can check, never by a note and never by a model. Unknown stays QUEUED.
  QUEUED    the default; the only state a note can create. A line that says "being built" changes nothing.
  STARTED   a lane folder exists for it, or a pull request / commit it names exists on GitHub.
  BLOCKED   that lane's report.md line 1 says BLOCKED or STEP-FOR-JONATHAN.
  DONE      that lane's report.md line 1 starts DONE or QUEUE EMPTY, a named pull request merged, or the item has a "done" note.
            (The line stays on the board until someone ticks it; the ledger says "close it".)
  AT-RISK   still QUEUED with a deadline (due: ...) that has passed, or is closer than the margin.
How an item names its evidence (all of it is looked up, none of it is believed):
  lane <name>  /  lane: <name>  /  the dated lane folder name   -> ~/.claude/state/lanes/<date>-<name>/ and its report.md line 1
  owner/repo#123  or  https://github.com/owner/repo/pull/123      -> gh pr view
  owner/repo@<commit>                                              -> gh api, the commit exists
A deadline is "due: 2026-11-07 1:00 PM", "due: 2026-11-07 13:00", "due: 1 PM" (the day the line was queued) or "due: 2026-11-08"
(a date alone means 5:00 PM that day). The margin is SHORT_MARGIN_H hours before a deadline that was set under a day ahead, LONG_MARGIN_H
hours before any other.

The board is read, never written (the `ledger --write` file is a separate snapshot). Nothing here ticks, moves or deletes a line.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from hub_inbox_archive import split_blocks  # noqa: E402

HOME = Path(os.environ.get("HOME", str(Path.home())))
STALE_DAYS = 10
EVIDENCE_OLD_H = 24
# The design proposed 3 hours. Jonathan's own example (queued, due 1 PM, checked at 9 AM) has to flag at 9 AM, and 3 hours would flag it at
# 10 AM, three hours before an ear-check that needs the morning, so the short margin is 4. The hub can change the number here.
SHORT_MARGIN_H = 4
LONG_MARGIN_H = 24
OWNERS = ["Waiting on Hatch", "Waiting on tower", "Waiting on NX-01", "Waiting on NCC-74656", "Job", "Client mail", "Client alert",
          "Client talk", "Question", "Hub"]

HEAD = re.compile(r"^- \[(?P<box>[ x])\] (?P<day>\d{4}-\d\d-\d\d)(?: (?P<hm>\d\d:\d\d))?(?: \| (?P<rest>.*))?$")
NOTE_DATE = re.compile(r"\d{4}-\d\d-\d\d")
LANE_REF = re.compile(r"\blane[ :=]+(?P<n>[a-z0-9][a-z0-9_-]{2,})", re.I)
LANE_DIR_REF = re.compile(r"\b(?P<n>\d{4}-\d\d-\d\d-[a-z0-9][a-z0-9_-]+)\b")
PR_REF = re.compile(r"(?:\b(?P<r1>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<n1>\d+)\b|github\.com/(?P<r2>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(?P<n2>\d+))")
COMMIT_REF = re.compile(r"\b(?P<r>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<sha>[0-9a-f]{7,40})\b")
DUE = re.compile(r"\bdue:?\s+(?:(?P<d>\d{4}-\d\d-\d\d)(?:[ T]+|\b))?(?:(?P<h>\d{1,2})(?::(?P<m>\d\d))?\s*(?P<ap>[ap]\.?m\.?)?)?", re.I)


class Item:
    def __init__(self, block):
        self.block = block
        self.head = block[0].rstrip("\n")
        m = HEAD.match(self.head)
        self.ok = bool(m)
        self.open = bool(m) and m.group("box") == " "
        self.created = None
        self.owner = "other"
        self.text = self.head
        if m:
            hm = m.group("hm") or "00:00"
            try:
                self.created = datetime.strptime(m.group("day") + " " + hm, "%Y-%m-%d %H:%M")
            except ValueError:
                self.created = None
            self.text = m.group("rest") or ""
            for o in OWNERS:
                if self.text.startswith(o + ":"):
                    self.owner = o
                    break
        self.notes = [ln.rstrip("\n") for ln in block[1:]]
        self.id = hashlib.sha1(self.head.encode("utf-8", "replace")).hexdigest()[:8]

    def full_text(self):
        return " ".join([self.text] + [n.strip() for n in self.notes])

    def last_touch(self):
        """The latest date on the head or on the first 30 characters of a note; None when the head has no readable date."""
        if self.created is None:
            return None
        best = self.created.date()
        for n in self.notes:
            d = NOTE_DATE.search(n[:30])
            if d:
                try:
                    v = datetime.strptime(d.group(0), "%Y-%m-%d").date()
                except ValueError:
                    continue
                best = max(best, v)
        return best

    def is_stale(self, today, days):
        t = self.last_touch()
        return t is not None and (today - t).days > days

    def done_note(self):
        # "  - done ...", "  - 2026-11-06 done: ..." or "  - 2026-11-06 10:00 done ..." (an optional date and time before the word)
        return any(re.match(r"\s+-\s*(?:\d{4}-\d\d-\d\d(?:\s+\d\d:\d\d)?\s+)?done\b", n, re.I) for n in self.notes)

    def decision(self):
        for n in self.notes:
            m = re.match(r"\s+-\s*(?:\d{4}-\d\d-\d\d[ \d:]*:?\s*)?decision:?\s*(.+)", n, re.I)
            if m:
                return m.group(1).strip()
        return None


def read_items(path):
    p = Path(os.path.realpath(path))
    if not p.exists():
        return []
    lines = p.read_text(errors="replace").splitlines(keepends=True)
    _, blocks = split_blocks(lines)
    return [it for it in (Item(b) for b in blocks) if it.ok and it.open]


# ---- evidence -------------------------------------------------------------------------------------------------------------------

def lane_folders(lanes_dir):
    try:
        return sorted(p for p in Path(lanes_dir).iterdir() if p.is_dir())
    except OSError:
        return []


def find_lane(item, folders):
    """-> the lane folder this item names, or None. The newest folder wins when a name was reused on several days."""
    text = item.full_text()
    names = set()
    for m in LANE_REF.finditer(text):
        names.add(m.group("n").lower())
    wanted = []
    for f in folders:
        base = f.name.lower()
        short = re.sub(r"^\d{4}-\d\d-\d\d-", "", base)
        if short in names or base in names:
            wanted.append(f)
    for m in LANE_DIR_REF.finditer(text):
        for f in folders:
            if f.name.lower() == m.group("n").lower() and f not in wanted:
                wanted.append(f)
    return sorted(wanted, key=lambda f: f.name)[-1] if wanted else None


def report_line1(folder):
    rp = folder / "report.md"
    try:
        for ln in rp.read_text(errors="replace").splitlines():
            if ln.strip():
                return ln.strip(), rp.stat().st_mtime
    except OSError:
        pass
    return None, None


def lane_state(line1):
    if line1 is None:
        return "STARTED", "lane folder exists, no report yet"
    up = line1.upper().lstrip("# *`")
    if up.startswith("DONE") or up.startswith("QUEUE EMPTY"):
        return "DONE", "report line 1: " + line1[:60]
    if up.startswith("BLOCKED") or up.startswith("STEP-FOR-JONATHAN"):
        return "BLOCKED", "report line 1: " + line1[:60]
    return "STARTED", "report line 1: " + line1[:60]


def gh_resolver(kind, repo, ref):
    """-> 'merged' | 'open' | 'closed' | 'exists' | None. Any failure is None (the item stays QUEUED)."""
    try:
        if kind == "pr":
            r = subprocess.run(["gh", "pr", "view", ref, "-R", repo, "--json", "state"], capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return None
            return {"MERGED": "merged", "OPEN": "open", "CLOSED": "closed"}.get(json.loads(r.stdout).get("state"))
        r = subprocess.run(["gh", "api", "repos/%s/commits/%s" % (repo, ref), "--jq", ".sha"], capture_output=True, text=True, timeout=15)
        return "exists" if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def parse_due(item):
    """-> a datetime or None. Reads the first "due" in the line and its notes."""
    m = DUE.search(item.full_text())
    if not m:
        return None
    d, h, mi, ap = m.group("d"), m.group("h"), m.group("m"), m.group("ap")
    if d is None and h is None:
        return None
    try:
        day = datetime.strptime(d, "%Y-%m-%d").date() if d else (item.created.date() if item.created else None)
    except ValueError:
        return None
    if day is None:
        return None
    if h is None:
        return datetime(day.year, day.month, day.day, 17, 0)
    hour, minute = int(h), int(mi or 0)
    if ap:
        hour = hour % 12 + (12 if ap[0].lower() == "p" else 0)
    elif mi is None:
        # A bare number is not a time ("due 3 things", "due 2026-11-08 12 items"): a date alone keeps its 5:00 PM, no date means no deadline.
        return datetime(day.year, day.month, day.day, 17, 0) if d else None
    if hour > 23 or minute > 59:
        return None
    return datetime(day.year, day.month, day.day, hour, minute)


def evaluate(item, now, folders, resolver, net=True):
    """-> dict(state, evidence, age_h, due). Facts only; nothing here reads what a note claims."""
    state, evidence, mtime = "QUEUED", "no lane, pull request or commit found for it", None
    if item.done_note():
        state, evidence = "DONE", "the item has a done note"
    else:
        lane = find_lane(item, folders)
        if lane is not None:
            line1, mtime = report_line1(lane)
            state, why = lane_state(line1)
            evidence = "lane %s: %s" % (lane.name, why)
            if mtime is None:
                try:
                    mtime = lane.stat().st_mtime
                except OSError:
                    mtime = None
        elif net:
            text = item.full_text()
            for m in PR_REF.finditer(text):
                repo, num = m.group("r1") or m.group("r2"), m.group("n1") or m.group("n2")
                got = resolver("pr", repo, num)
                if got == "merged":
                    state, evidence = "DONE", "%s#%s merged" % (repo, num)
                    break
                if got == "open":
                    state, evidence = "STARTED", "%s#%s is open" % (repo, num)
            if state == "QUEUED":
                for m in COMMIT_REF.finditer(text):
                    if resolver("commit", m.group("r"), m.group("sha")):
                        state, evidence = "STARTED", "commit %s@%s exists" % (m.group("r"), m.group("sha")[:7])
                        break
    age_h = None
    if mtime is not None:
        age_h = max(0, int((now.timestamp() - mtime) // 3600))
        if age_h >= EVIDENCE_OLD_H:
            evidence += " (evidence %d h old)" % age_h
    due = parse_due(item)
    if state == "QUEUED" and due is not None:
        short = item.created is not None and (due - item.created) < timedelta(days=1)
        margin = timedelta(hours=SHORT_MARGIN_H if short else LONG_MARGIN_H)
        if due - now <= margin:
            state = "AT-RISK"
            evidence = "still queued, " + evidence
    return {"state": state, "evidence": evidence, "age_h": age_h, "due": due}


# ---- output ---------------------------------------------------------------------------------------------------------------------

def t12(dt):
    return dt.strftime("%a %-d %b %-I:%M %p")


def left(due, now):
    secs = int((due - now).total_seconds())
    if secs <= 0:
        return "overdue by %s" % hours_words(-secs)
    return "%s left" % hours_words(secs)


def hours_words(secs):
    h = secs // 3600
    if h >= 48:
        return "%d days" % (h // 24)
    if h >= 1:
        return "%d h" % h
    return "%d min" % max(1, secs // 60)


def short(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def context(a):
    now = datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else datetime.now().replace(second=0, microsecond=0)
    items = read_items(a.inbox)
    folders = lane_folders(a.lanes_dir)
    return now, items, folders


def fresh(items, now, days):
    return [i for i in items if not i.is_stale(now.date(), days)]


def cmd_counts(a):
    now, items, folders = context(a)
    chars = sum(len("".join(i.block)) for i in items)
    stale = [i for i in items if i.is_stale(now.date(), a.stale_days)]
    fresh_items = [i for i in items if i not in stale]
    print("open items: %d (%d characters, about %d tokens if read whole)" % (len(items), chars, chars // 4))
    sc = sum(len("".join(i.block)) for i in stale)
    print("  fresh: %d (%d characters, about %d tokens)" % (len(fresh_items), chars - sc, (chars - sc) // 4))
    print("  stale (older than %d days, no change since): %d (%d characters, about %d tokens)" % (a.stale_days, len(stale), sc, sc // 4))
    by = {}
    for i in items:
        by[i.owner] = by.get(i.owner, 0) + 1
    print("by owner: " + ", ".join("%s %d" % (k, v) for k, v in sorted(by.items(), key=lambda kv: -kv[1])))
    st = {}
    for i in items:
        e = evaluate(i, now, folders, gh_resolver, net=not a.no_net)
        st[e["state"]] = st.get(e["state"], 0) + 1
    print("by state: " + ", ".join("%s %d" % (k, v) for k, v in sorted(st.items())))
    buckets = [("0-7 days", 0, 7), ("8-14 days", 8, 14), ("15-30 days", 15, 30), ("31+ days", 31, 10 ** 6)]
    ages = {b[0]: 0 for b in buckets}
    undated = 0
    for i in items:
        t = i.last_touch()
        if t is None:
            undated += 1
            continue
        d = (now.date() - t).days
        for name, lo, hi in buckets:
            if lo <= d <= hi:
                ages[name] += 1
    print("by days since last change: " + ", ".join("%s %d" % (k, v) for k, v in ages.items()) + (", undated %d" % undated if undated else ""))
    return 0


def cmd_stale(a):
    now, items, _ = context(a)
    n = 0
    for i in items:
        if i.is_stale(now.date(), a.stale_days):
            n += 1
            print("%s %s | untouched since %s | %s" % (i.id, i.created.strftime("%Y-%m-%d") if i.created else "?", i.last_touch(), short(i.text, 160)))
    if n == 0:
        print("no stale open items (older than %d days with no change)" % a.stale_days)
    return 0


def pick(items, a, now):
    out = items if a.include_stale else fresh(items, now, a.stale_days)
    if a.owner:
        out = [i for i in out if i.text.lower().startswith(a.owner.lower())]
    if getattr(a, "match", None):
        out = [i for i in out if a.match.lower() in i.full_text().lower()]
    return out


def judged(a):
    """-> (now, [(item, evaluation)]) for the items picked by the flags, narrowed by --state (a comma list of state words)."""
    now, items, folders = context(a)
    want = {w.strip().upper() for w in (a.state or "").split(",") if w.strip()}
    out = []
    for i in pick(items, a, now):
        e = evaluate(i, now, folders, gh_resolver, net=not a.no_net)
        if not want or e["state"] in want:
            out.append((i, e))
    return now, out


def cmd_ledger(a):
    now, judged_items = judged(a)
    rows = []
    for i, e in judged_items:
        due = "due %s (%s)" % (t12(e["due"]), left(e["due"], now)) if e["due"] else "no deadline"
        rows.append("%s %-8s %s | %s | %s | %s" % (i.id, e["state"], i.owner, short(i.text, 70), due, short(e["evidence"], 80)))
    text = "\n".join(rows) + ("\n" if rows else "")
    if a.write:
        tmp = Path(a.write).with_name("." + Path(a.write).name + ".tmp")
        tmp.write_text("# Notes ledger, made %s by notes_ledger.py (a snapshot; the board is the record)\n%s" % (t12(now), text))
        os.replace(tmp, a.write)
        print("wrote %d line(s) to %s" % (len(rows), a.write))
    else:
        sys.stdout.write(text or "no open items\n")
    return 0


def cmd_order(a):
    now, chosen = judged(a)
    if a.limit:
        chosen = chosen[: a.limit]
    if not chosen:
        print("no open items match")
        return 0
    for i, e in chosen:
        print("[%s] %s  STATE: %s" % (i.owner, i.id, e["state"]))
        print("  item: %s" % short(i.text, 300))
        print("  decision already made: %s" % (short(i.decision(), 200) if i.decision() else "none recorded on the line"))
        print("  deadline: %s" % ("%s (%s)" % (t12(e["due"]), left(e["due"], now)) if e["due"] else "none on the line"))
        print("  evidence: %s" % e["evidence"])
        if e["state"] == "DONE":
            print("  note: the evidence says this is finished; the line is still open on the board, so close it")
        print()
    return 0


def cmd_risk(a):
    now, items, folders = context(a)
    n = 0
    for i in fresh(items, now, a.stale_days):
        e = evaluate(i, now, folders, gh_resolver, net=not a.no_net)
        if e["state"] == "AT-RISK":
            n += 1
            print("AT-RISK %s [%s] %s | due %s, %s | %s" % (i.id, i.owner, short(i.text, 90), t12(e["due"]), left(e["due"], now), e["evidence"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("cmd", choices=["counts", "stale", "ledger", "order", "risk"])
    ap.add_argument("--inbox", default=os.environ.get("HUB_INBOX") or os.environ.get("MUSE_INBOX") or str(HOME / ".claude/state/hub-inbox.md"))
    ap.add_argument("--lanes-dir", default=str(HOME / ".claude/state/lanes"))
    ap.add_argument("--stale-days", type=int, default=int(os.environ.get("HUB_STALE_DAYS", STALE_DAYS)))
    ap.add_argument("--now", help="YYYY-MM-DD HH:MM (tests)")
    ap.add_argument("--no-net", action="store_true", help="do not ask GitHub about pull requests or commits")
    ap.add_argument("--owner")
    ap.add_argument("--match")
    ap.add_argument("--state", help="only these states, comma list: QUEUED,STARTED,DONE,BLOCKED,AT-RISK")
    ap.add_argument("--include-stale", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--write")
    a = ap.parse_args(argv)
    return {"counts": cmd_counts, "stale": cmd_stale, "ledger": cmd_ledger, "order": cmd_order, "risk": cmd_risk}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
