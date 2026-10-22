#!/usr/bin/env python3
"""A live tracker for the path from intern to Jr Dev at TechJoy.

  internship_status.py [--ledger F] [--digest | --json] [--now ISO]

TechJoy's rules, as the tracker reads them:
  - A ticket counts when it goes from pickup (assigned to the user, card to In progress) to review (PR up, card to
    Peer review) within 7 days. Two LEVEL II tickets picked up together count as one LEVEL III.
  - Ready for the Jr Dev review: one LEVEL III done in a week and its PR merged into main.
  - Jr Dev bar: that L3, plus at least 2 other tickets each done in a week, 50+ lines across 10+ files, and 3+ weeks
    of consistency.

The ledger (default ~/repos/hub/internship/tickets.tsv, private, never copied into a public repo) has the columns
program, repo, issue, pr, level, level_source, picked_up, in_review, merged, loom, reflection, notes. Times are
ISO 8601 and any cell may be empty. The ledger wins over GitHub for the level; GitHub fills what the ledger leaves
blank (issue labels, the first "assigned" event to the user as pickup, the PR's created_at as review, merged_at,
reviewers, files and lines). GitHub is reached only through the gh CLI, which holds its own token: nothing here
reads or prints one. If gh fails the output is one line, "internship: gh unavailable", and the exit code is 0, so
the hub digest never breaks.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
LEDGER_DEFAULT = HOME / "repos/hub/internship/tickets.tsv"
USER = "TortoiseWolfe"
WEEK_DAYS = 7.0
PAIR_HOURS = 24.0
MIN_FILES, MIN_LINES, OTHERS_NEEDED, WEEKS_NEEDED, WEEKS_WINDOW = 10, 50, 2, 3, 4
UNAVAILABLE = "internship: gh unavailable"
LEVELS = {"0": 0, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
LEVEL_RE = re.compile(r"\bLEVEL\s+(0|III|II|IV|V|I)\b", re.I)
LIVE_FIELDS = ("labels", "assigned_at", "pr_created", "merged_at", "base_ref", "review", "reviewers",
               "changed_files", "additions", "deletions")


class GhError(Exception):
    """gh could not answer (not installed, not logged in, no network, a 404)."""


# ---------------------------------------------------------------------------------------------
# The gh layer: the only place that reaches the network. Tests replace it with a function of the same shape.
# ---------------------------------------------------------------------------------------------

def decode_pages(text):
    """gh --paginate prints one JSON value per page, back to back (older gh has no --slurp)."""
    dec, i, pages = json.JSONDecoder(), 0, []
    text = text.strip()
    while i < len(text):
        value, end = dec.raw_decode(text, i)
        pages.append(value)
        i = end
        while i < len(text) and text[i].isspace():
            i += 1
    if pages and all(isinstance(p, list) for p in pages):
        return [item for p in pages for item in p]
    return pages[0] if len(pages) == 1 else pages


def gh_api(path):
    try:
        r = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GhError(type(exc).__name__)
    if r.returncode != 0:
        raise GhError("gh exited %d" % r.returncode)
    try:
        return decode_pages(r.stdout)
    except ValueError:
        raise GhError("unreadable answer")


def review_state(reviews):
    """The state that counts: each reviewer's last verdict, changes-requested over approved over nothing."""
    last = {}
    for rv in reviews:
        if rv.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[(rv.get("user") or {}).get("login")] = rv["state"]
    states = set(last.values())
    if "CHANGES_REQUESTED" in states:
        return "changes requested"
    if "APPROVED" in states:
        return "approved"
    return ""


def fetch_live(row, api=gh_api):
    """What GitHub knows about one ledger row. Raises GhError."""
    repo, out = row["repo"], {k: None for k in LIVE_FIELDS}
    if row.get("issue"):
        out["labels"] = [lb["name"] for lb in api("repos/%s/issues/%s" % (repo, row["issue"])).get("labels", [])]
        for ev in api("repos/%s/issues/%s/timeline" % (repo, row["issue"])):
            if ev.get("event") == "assigned" and (ev.get("assignee") or {}).get("login") == USER:
                out["assigned_at"] = ev.get("created_at")
                break
    if row.get("pr"):
        pr = api("repos/%s/pulls/%s" % (repo, row["pr"]))
        out.update(pr_created=pr.get("created_at"), merged_at=pr.get("merged_at"), base_ref=(pr.get("base") or {}).get("ref"),
                   reviewers=[u.get("login") for u in pr.get("requested_reviewers", [])],
                   changed_files=pr.get("changed_files"), additions=pr.get("additions"), deletions=pr.get("deletions"),
                   review=review_state(api("repos/%s/pulls/%s/reviews" % (repo, row["pr"]))))
    return out


# ---------------------------------------------------------------------------------------------
# The ledger and the numbers
# ---------------------------------------------------------------------------------------------

def parse_time(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def parse_level(text):
    """'LEVEL III', 'III', 'L3' or '3' -> 3; None when it says nothing."""
    text = (text or "").strip()
    m = LEVEL_RE.search(text) or re.fullmatch(r"(?:L\s*)?(0|III|II|IV|V|I)", text, re.I)
    if m:
        return LEVELS.get(m.group(1).upper())
    m = re.fullmatch(r"(?:L\s*)?([0-5])", text, re.I)
    return int(m.group(1)) if m else None


def read_ledger(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh, delimiter="\t")
                if (row.get("repo") or "").strip() and (row.get("issue") or "").strip()]


def build_ticket(row, live, now):
    live = live or {}
    level = parse_level(row.get("level"))
    if level is None:
        level = next((parse_level(lb) for lb in (live.get("labels") or []) if LEVEL_RE.search(lb)), None)
    picked = parse_time(row.get("picked_up")) or parse_time(live.get("assigned_at"))
    review = parse_time(row.get("in_review")) or parse_time(live.get("pr_created"))
    merged_at = parse_time(row.get("merged")) or parse_time(live.get("merged_at"))
    base = live.get("base_ref")
    merged = merged_at is not None and (base in (None, "main", "master"))
    days = (review - picked).total_seconds() / 86400 if picked and review else None
    t = {"repo": row["repo"], "issue": int(row["issue"]), "pr": int(row["pr"]) if row.get("pr") else None, "level": level,
         "picked_up": picked, "in_review": review, "merged": merged,
         "days_to_review": days, "in_week": days is not None and 0 <= days <= WEEK_DAYS,
         "wait_days": (now - review).total_seconds() / 86400 if review and not merged else None,
         "reviewers": live.get("reviewers") or [], "review": live.get("review") or "",
         "files": live.get("changed_files") or 0, "lines": (live.get("additions") or 0) + (live.get("deletions") or 0)}
    t["open"] = bool(picked and not review)
    t["clock_days"] = (now - picked).total_seconds() / 86400 if t["open"] else None
    t["not_started"] = not (picked or review or t["pr"])
    return t


def label(t):
    return "%s #%d" % (t["repo"].split("/")[-1], t["issue"])


def candidate_units(tickets):
    """Things that can be the L3: an in-week L3, or two in-week L2s in the same repo picked up within 24 hours."""
    units = [[t] for t in tickets if t["in_week"] and (t["level"] or 0) >= 3]
    l2 = [t for t in tickets if t["in_week"] and t["level"] == 2]
    for i, a in enumerate(l2):
        for b in l2[i + 1:]:
            if a["repo"] == b["repo"] and abs((a["picked_up"] - b["picked_up"]).total_seconds()) <= PAIR_HOURS * 3600:
                units.append([a, b])
    return units


def checklist(tickets, now):
    units = candidate_units(tickets)
    unit = max(units, key=lambda u: (all(t["merged"] for t in u), sum(t["files"] for t in u), len(u)), default=None)
    ids = {id(t) for t in unit or []}
    others = [t for t in tickets if t["in_week"] and id(t) not in ids]
    files, lines = sum(t["files"] for t in unit or []), sum(t["lines"] for t in unit or [])
    cutoffs = [now - timedelta(days=WEEK_DAYS * (k + 1)) for k in range(WEEKS_WINDOW)]
    weeks = sum(1 for k in range(WEEKS_WINDOW) if any(t["in_week"] and cutoffs[k] < t["in_review"] <= cutoffs[k] + timedelta(days=WEEK_DAYS)
                                                      for t in tickets))
    return {"unit": [label(t) for t in unit or []],
            "a_l3_in_a_week": unit is not None,
            "b_merged": bool(unit) and all(t["merged"] for t in unit),
            "c_other_in_week": len(others), "c_ok": len(others) >= OTHERS_NEEDED,
            "d_files": files, "d_lines": lines, "d_ok": files >= MIN_FILES and lines >= MIN_LINES,
            "e_weeks": weeks, "e_ok": weeks >= WEEKS_NEEDED}


def open_groups(tickets):
    """Open (picked up, not yet in review) tickets; same-repo ones picked up within 24 hours are one group."""
    groups = []
    for t in sorted((t for t in tickets if t["open"]), key=lambda t: t["picked_up"]):
        for g in groups:
            if g[0]["repo"] == t["repo"] and abs((g[0]["picked_up"] - t["picked_up"]).total_seconds()) <= PAIR_HOURS * 3600:
                g.append(t)
                break
        else:
            groups.append([t])
    return groups


def summarize(tickets, now):
    waiting = [t for t in tickets if t["wait_days"] is not None]
    longest = max(waiting, key=lambda t: t["wait_days"], default=None)
    return {"checklist": checklist(tickets, now),
            "open": [{"tickets": [label(t) for t in g], "clock_days": max(t["clock_days"] for t in g),
                      "days_left": WEEK_DAYS - max(t["clock_days"] for t in g)} for g in open_groups(tickets)],
            "not_started": [label(t) for t in tickets if t["not_started"]],
            "longest_wait": longest and {"ticket": label(longest), "pr": longest["pr"], "days": longest["wait_days"],
                                         "held_by": ", ".join(longest["reviewers"]) or "nobody named"},
            "tickets": [{"ticket": label(t), "pr": t["pr"], "level": t["level"], "days_to_review": t["days_to_review"],
                         "in_week": t["in_week"], "merged": t["merged"], "wait_days": t["wait_days"]} for t in tickets]}


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------

def tick(ok):
    return "[x]" if ok else "[ ]"


def digest_lines(s):
    c = s["checklist"]
    lines = ["Jr Dev: %s L3 in a week%s, %s merged, %s 2 others in a week (%d), %s 10+ files and 50+ lines (%d files, %d lines), %s 3+ of the last 4 weeks (%d)"
             % (tick(c["a_l3_in_a_week"]), " (" + " + ".join(c["unit"]) + ")" if c["unit"] else "", tick(c["b_merged"]),
                tick(c["c_ok"]), c["c_other_in_week"], tick(c["d_ok"]), c["d_files"], c["d_lines"], tick(c["e_ok"]), c["e_weeks"])]
    if s["open"]:
        g = max(s["open"], key=lambda g: g["clock_days"])
        left = g["days_left"]
        lines.append("Clock: %s picked up %.1f days ago, %s" % (" + ".join(g["tickets"]), g["clock_days"],
                     "%.1f days left to get the PR up" % left if left >= 0 else "%.1f days past the 7-day week" % -left))
    elif s["not_started"]:
        lines.append("Clock: not running; next pick %s, not started" % " + ".join(s["not_started"][:2]))
    else:
        lines.append("Clock: no ticket open")
    w = s["longest_wait"]
    lines.append("Longest review wait: %s PR #%s, %.1f days, held by %s" % (w["ticket"], w["pr"], w["days"], w["held_by"]) if w
                 else "Longest review wait: none")
    return lines[:4]


def report_lines(s):
    out = digest_lines(s) + ["", "Tickets:"]
    for t in s["tickets"]:
        d = "-" if t["days_to_review"] is None else "%.1f d" % t["days_to_review"]
        out.append("  %s PR %s level %s: pickup to review %s, %s, %s%s" % (
            t["ticket"], t["pr"] or "-", t["level"] if t["level"] is not None else "?", d,
            "in a week" if t["in_week"] else "not counted", "merged" if t["merged"] else "not merged",
            ", waiting %.1f d" % t["wait_days"] if t["wait_days"] is not None else ""))
    return out


def main(argv=None, fetch=fetch_live, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=str(LEDGER_DEFAULT))
    ap.add_argument("--now", help="ISO time to measure from (tests)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--digest", action="store_true", help="at most 4 lines")
    mode.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    now = now or parse_time(a.now) or datetime.now(timezone.utc)
    try:
        rows = read_ledger(a.ledger)
    except OSError as exc:
        print("internship: ledger unreadable (%s)" % type(exc).__name__)
        return 0
    try:
        tickets = [build_ticket(r, fetch(r), now) for r in rows]
    except GhError:
        print(UNAVAILABLE)
        return 0
    s = summarize(tickets, now)
    if a.json:
        print(json.dumps(s, indent=2, default=str))
    else:
        print("\n".join(digest_lines(s) if a.digest else report_lines(s)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
