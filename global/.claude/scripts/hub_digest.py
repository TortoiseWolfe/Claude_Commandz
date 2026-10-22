#!/usr/bin/env python3
"""Build the hub's status page: one small private file the Claude hub reads at /prep instead of
re-reading the roadmap body, MEMORY.md, the whole muse inbox and the briefing files.

  hub_digest.py [--out FILE] [--cap N] [--now ISO] [--roadmap-file F] [--lane-status-file F]
                [--lanes-dir D] [--inbox F] [--git-repo D] [--tidy-file F] [--budget-file F]
                [--report-hours H]

No model calls and no Gmail (the client-mail poller already checks Gmail and queues "Client mail:" /
"Client alert:" lines in the hub inbox). Sources, all local except one gh call:
  - lane.sh --status and the lane report.md files written in the last day (first line of each);
  - open receptionist questions, then hub-inbox items grouped by who we wait on (OpenClaw, Hatch, the tower,
    Jonathan, jobs, hub, other), a wait older than 5 days marked "chase or close",
    with the open client lines listed apart;
  - the roadmap issue: active arc, the Next 3 sessions, "Next session should";
  - the Jr Dev tracker's --digest lines (internship_status.py, 20 s limit; no section if the script is missing);
  - git log -10 of ~/repos, hub/scripts/tidy-report.sh, and the week's budget headline from
    hub/scripts/model_quality_report.py (cached for an hour: the report takes ~15 s).

Private by construction: it stays in ~/.claude/state and nothing sends it anywhere (the OpenClaw digest
is a different file, built by openclaw_status_digest.py with its own filter). As a second guard, key-,
token- and reset-link-shaped text is scrubbed from every line before it is written.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from openclaw_status_digest import clock12, parse_lane_status, parse_roadmap  # noqa: E402

CAP_DEFAULT = 5800  # the page must stay under 6 KB
BUDGET_TTL = 3600
REPORT_WORD = {"yes": "written", "stale": "stale (new input or a job still running; not done)"}
GIT_DATE = "format:%a %-I:%M %p"  # git log --date value: 12-hour clock
HUB_REPO = "TortoiseWolfe/workspace"

# ---------------------------------------------------------------------------------------------
# Scrubbing: nothing key-, token- or reset-link-shaped goes onto the page
# ---------------------------------------------------------------------------------------------

SECRETS = [
    re.compile(r"https?://\S*(?:reset|recover|token|verify|magic|invite|unsubscribe)\S*", re.I),
    re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}|\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)[?&;](?:token|key|code|secret|apikey|api_key|password|passwd|auth|sig)=[^\s&]+"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S{6,}"),
    re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9])"),
]


def scrub(text):
    for rx in SECRETS:
        text = rx.sub("[redacted]", text)
    return text


def one_line(text, limit):
    t = re.sub(r"<[^>]{1,80}>", " ", text or "")
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = t.replace("**", "").replace("`", "")
    t = clock12(re.sub(r"\s+", " ", t).strip())
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-(") + " ..."
    return scrub(t)


# ---------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------


def _run(cmd, timeout=40, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def dispatcher_open_questions(dispatcher=None):
    """Read the authoritative queue; return an error separately so a failed read is never shown as an empty queue."""
    dispatcher = dispatcher or Path(__file__).with_name("openclaw_dispatch.py")
    try:
        result = subprocess.run(["python3", str(dispatcher), "open"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", "could not run dispatcher open: %s" % exc
    if result.returncode != 0:
        return "", "dispatcher open exited %d: %s" % (result.returncode, " ".join(result.stderr.split())[-300:])
    return result.stdout, None


# ---------------------------------------------------------------------------------------------
# The live-state check: the code that reads the roadmap issue and the hub-inbox must notice when the live text no longer parses
# ---------------------------------------------------------------------------------------------

PAGE_HEADINGS = ["## Lanes running", "## Open hub-inbox items", "## Roadmap", "## Budget this week",
                 "## ~/repos, last 10 commits", "## Tidy report"]
PAGE_HARD_CAP = 6144


def live_problems(roadmap_body, page):
    """What is wrong with the page built from the live roadmap body, or [] when the live state still parses. These were the asserts of
    a test that only ran against the real state (and so only on one machine, when someone set an env var); they run here on every
    live build instead, so a roadmap edit that breaks parse_roadmap is noticed within the quarter hour."""
    probs = []
    if not (roadmap_body or "").strip():
        return ["the roadmap issue body came back empty (gh failed or the issue changed)"]
    rm = parse_roadmap(roadmap_body)
    if not rm["next_session"]:
        probs.append('no "### Next session should" text parsed from the roadmap')
    if not rm["arcs"]:
        probs.append("no Arc summary parsed from the roadmap")
    if not rm["next3"]:
        probs.append('no "Next 3 sessions" block parsed from the roadmap')
    if len(page.encode()) >= PAGE_HARD_CAP:
        probs.append("the page is %d bytes, over the %d cap" % (len(page.encode()), PAGE_HARD_CAP))
    probs.extend('the page lacks the heading "%s"' % h for h in PAGE_HEADINGS if h not in page)
    if "roadmap unavailable" in page:
        probs.append("the page says the roadmap is unavailable")
    if any(rx.search(page) for rx in SECRETS):
        probs.append("secret-shaped text survived the scrub")
    return probs


def alert_hub(key, problems, source, now=None, state_path=None):
    """Fail loudly: put the problem on the hub's list the way a question the front desk could not answer goes (an ask record, a hub-inbox
    line and the wake file lane_watch.sh watches), so the hub is woken and sees it at /prep. One alert per key and day: a retry every
    15 minutes files nothing twice. state_path: where the dispatch state lives (tests pass a temp one). -> the ask id, or None when it
    was filed already today."""
    import hashlib

    import openclaw_dispatch as dispatch

    now = time.time() if now is None else now
    ctx = dispatch.Ctx({}, state_path=state_path or dispatch.state_file())
    ask_id = dispatch.ask_id_for(hashlib.sha256(("%s %s" % (key, time.strftime("%Y-%m-%d", time.localtime(now)))).encode("utf-8")).hexdigest())
    words = ("ALERT from %s, not a message of his: the live state no longer parses. %s. "
             "Fix the parser or the text it reads, then close this." % (source, "; ".join(problems)))
    if dispatch.file_with_hub(ctx, ask_id, words, "hub, a live-state check failed", "%s: live state parse check" % source, now):
        return ask_id
    return None


def report_first_line(path, limit=140):
    """First line of a lane report.md that says something: skips blanks and heading marks."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    fallback = ""
    for ln in lines:
        s = ln.strip()
        if not s or set(s) <= set("-=* _"):
            continue
        if s.startswith("#"):
            fallback = fallback or s.lstrip("# ").strip()
            continue
        return one_line(s, limit)
    return one_line(fallback, limit)


def lane_reports(lanes_dir, now, hours):
    """-> [(mtime, lane name, first line)] newest first, for report.md files written in the window."""
    out = []
    for p in Path(lanes_dir).glob("*/report.md"):
        try:
            mt = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if now - mt > timedelta(hours=hours):
            continue
        name = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", p.parent.name)
        out.append((mt, name, report_first_line(p)))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


# The canonical owner prefixes (muse_poll_record.sh refuses any other): Waiting on Hatch, Waiting on tower, Job, Client mail,
# Client alert, Question, Hub. "Jonathan via OpenClaw" is the receptionist's own line, a Question in all but name; older lines
# used "Waiting on Jonathan" and "[CC>MUSE]".
GROUPS = [("OpenClaw", re.compile(r"^Jonathan via OpenClaw\b", re.I)),
          ("Hatch", re.compile(r"^waiting on hatch|^\[CC>MUSE\]", re.I)),
          ("the tower", re.compile(r"^waiting on (?:the )?tower", re.I)),
          ("Jonathan", re.compile(r"^waiting on jonathan|^question:", re.I)),
          ("jobs", re.compile(r"^job:", re.I)),
          ("hub", re.compile(r"^hub:", re.I))]
STALE_WAIT_DAYS = 5   # a "Waiting on ..." line older than this is flagged: the hub sends one reminder or closes it with a reason
OWNER_PREFIX = re.compile(r"^(?:waiting on |job:|question:|hub:)", re.I)  # the canonical owners (the legacy "[CC>MUSE]" start stays under other when a line is unreadable)
CLIENT_LINE = re.compile(r"^client (?:mail|alert):", re.I)


OPEN_LINE = re.compile(r"- \[ \] (\d{4}-(\d\d-\d\d)) [\d:]+ \| ([^|]*?) \|(?: ([^|]*?) \|)?(?: ([^|]*?) \|)?"
                       r"(?: ([\w-]+(?: [\w-]+)*))?\s*$")
OPEN_ANY = re.compile(r"- \[ \] (?:\d{4}-(\d\d-\d\d) [\d:]+ \| )?(.*)$")
CLIENT_SCAN = re.compile(r"^- \[ \] .*\bClient (?:mail|alert):")


def count_open_client_lines(text):
    """Plain scan, no line pattern: unticked lines that mention a client mail or alert anywhere."""
    return sum(1 for line in text.splitlines() if CLIENT_SCAN.match(line))


def wait_age_days(day, now):
    """Days since an inbox line's "MM-DD" stamp, read in now's year (or the year before when that date is still ahead)."""
    try:
        d = datetime(now.year, int(day[:2]), int(day[3:5]))
    except ValueError:
        return 0
    if d > now:
        d = d.replace(year=now.year - 1)
    return (now - d).days


def open_inbox(text):
    """-> (groups {name: [(date, title)]}, client [(date, title, detail, ids)]). Only unticked items.
    The last field takes one or more space-separated IDs. An unticked line the pattern cannot read is
    never dropped: it goes into "other" with its raw text."""
    groups = {g: [] for g, _ in GROUPS}
    groups["other"] = []
    clients = []
    for line in text.splitlines():
        m = OPEN_LINE.match(line)
        if not m:
            raw = OPEN_ANY.match(line)
            if raw:
                day, text = raw.group(1) or "?", raw.group(2).strip()
                title = text.split(" | ")[0].strip()  # a line with more fields than OPEN_LINE reads still has its owner prefix up front
                for g, rx in GROUPS:
                    if OWNER_PREFIX.match(title) and rx.match(title):
                        groups[g].append((day, re.sub(r"^waiting on \w+:\s*", "", title, flags=re.I)))
                        break
                else:
                    groups["other"].append((day, text))
            continue
        day, title = m.group(2), m.group(3).strip()
        detail = " | ".join(part.strip() for part in (m.group(4), m.group(5)) if part)
        ident = m.group(6) or ""
        if CLIENT_LINE.match(title):
            clients.append((day, title, detail, ident))
            continue
        for g, rx in GROUPS:
            if rx.match(title):
                groups[g].append((day, re.sub(r"^waiting on \w+:\s*", "", title, flags=re.I)))
                break
        else:
            groups["other"].append((day, title))
    return groups, clients


def budget_headline(report_text):
    """One line from model_quality_report.py's markdown: the week, what is used, the burn and the projection."""
    def cell(label):
        m = re.search(rf"^\|\s*{re.escape(label)}[^|]*\|\s*([^|]+?)\s*\|", report_text, re.M)
        return m.group(1) if m else ""
    week = re.search(r"^Week .*?to reset (.+?)(?: \(.*)?\.?$", report_text, re.M)
    used, burn, proj = cell("Used so far"), cell("Burn, last 24"), cell("Projected at reset")
    if not used:
        return ""
    wk = f"Week resets {clock12(week.group(1).rstrip('.'))}" if week else "This week"
    return f"{wk}: used {used}, burn {burn or '?'}, projected {proj or '?'} (24 h rate)"


def budget_line(budget_file, now_ts, ttl=BUDGET_TTL):
    p = Path(budget_file)
    try:
        fresh = now_ts - p.stat().st_mtime < ttl
    except OSError:
        fresh = False
    if not fresh:
        rep = _run(["python3", str(HOME / "repos/hub/scripts/model_quality_report.py")], timeout=45)
        line = budget_headline(rep)
        if line:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(line + "\n")
            return line
    try:
        return p.read_text().strip() or "budget report unavailable"
    except OSError:
        return "budget report unavailable"


def tidy_summary(text):
    """tidy-report.sh prints '## heading' then item lines; keep the heading and a count."""
    out, head, n = [], None, 0
    for line in text.splitlines() + ["## end"]:
        if line.startswith("## "):
            if head is not None:
                out.append(f"{head}: {n}")
            head, n = line[3:].strip(), 0
        elif line.strip():
            n += 1
    return out


# ---------------------------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------------------------


def _stamp(now):
    return now.strftime("%a %d %b %-I:%M %p") + " " + time.tzname[time.localtime().tm_isdst > 0]


SPEND_TOP = 3


def spend_section(spend):
    """Top lanes by API-equivalent spend today (lane_spend.py --json). 21 Oct: $303 went in one day, $85 of it on one mail lane, unseen.
    spend: the parsed JSON, a string (why it is unavailable), or None (not measured)."""
    title = f"Lane spend today (API-equivalent, top {SPEND_TOP}; a lane past $25 wakes the hub)"
    if isinstance(spend, str):
        return (title, [f"- unavailable: {one_line(spend, 200)}"])
    if not isinstance(spend, dict):
        return (title, ["- not measured"])
    lanes = spend.get("lanes") or []
    lines = [f"- {l['name']}: ${l['usd']:.0f} ({'+'.join(sorted(l.get('families') or {}, key=lambda k: -l['families'][k]))})"
             for l in lanes[:SPEND_TOP]]
    lines.append(f"- all lanes and the hub together: ${spend.get('total', 0):.0f}")
    return (title, lines)


def internship_lines(script=None, timeout=20):
    """The Jr Dev tracker's digest lines; none when the script is missing (nothing is added then)."""
    script = Path(script) if script else Path(__file__).resolve().parent / "internship_status.py"
    if not script.exists():
        return []
    return [l for l in _run([sys.executable, str(script), "--digest"], timeout=timeout).splitlines() if l.strip()]


def build_sections(*, roadmap_body, lane_status, lanes_dir, inbox_text, git_log, tidy_text, budget, now,
                   report_hours=24, open_questions_text="", open_questions_error=None, spend=None, internship=None):
    sections = []

    if open_questions_error:
        sections.append(("Open receptionist questions (unavailable)", [f"- {one_line(open_questions_error, 240)}"]))
    else:
        questions = [one_line(line, 210) for line in (open_questions_text or "").splitlines()
                     if line.strip() and not line[0].isspace() and line.strip() != "none"]
        sections.append((f"Open receptionist questions ({len(questions)})", [f"- {line}" for line in questions] or ["- none"]))

    # Lanes
    live, dead = parse_lane_status(lane_status or "")
    lines = [f"- {n}: {st}, report {REPORT_WORD.get(rep, 'not yet')}" for n, rep, st in live]
    if dead:
        lines.append(f"- {dead} older lanes have ended")
    sections.append(("Lanes running", lines or ["- none"]))

    sections.append(spend_section(spend))

    reps = lane_reports(lanes_dir, now, report_hours)
    rl = [f"- {mt.strftime('%a %-I:%M %p')} {name}: {one_line(first, 105) or 'report written'}" for mt, name, first in reps[:8]]
    if len(reps) > 8:
        rl.append(f"- and {len(reps) - 8} more reports in the last {report_hours} h")
    sections.append((f"Lane reports, last {report_hours} h (first line; the file has the rest)", rl or ["- none"]))

    # Inbox
    groups, clients = open_inbox(inbox_text or "")
    cl = [f"- {d} {one_line(t, 60)} | {one_line(det, 50)} | {i}" for d, t, det, i in clients]
    seen = count_open_client_lines(inbox_text or "")
    if seen != len(clients):
        cl.insert(0, f"WARNING: {seen} open client lines in the inbox, {len(clients)} parsed: grep hub-inbox.md")
    sections.append(("Open client lines (the poller queued them; read the thread before an absorb lane)", cl or ["- none"]))
    if internship:
        sections.append(("Jr Dev", [f"- {one_line(l, 230)}" for l in internship[:4]]))
    il = []
    for g in ["OpenClaw", "Hatch", "the tower", "Jonathan", "jobs", "hub", "other"]:
        items = groups[g]
        if not items:
            continue
        waits = g in ("Hatch", "the tower", "Jonathan")
        il.append(f"{'Waiting on ' + g if waits else g.capitalize()} ({len(items)}):")
        for d, t in items[:5]:
            age = wait_age_days(d, now) if waits else 0
            il.append(f"- {d} {one_line(t, 105)}" + (f" ({age} days: chase or close)" if age > STALE_WAIT_DAYS else ""))
        if len(items) > 5:
            il.append(f"- and {len(items) - 5} more")
    sections.append(("Open hub-inbox items", il or ["- none"]))

    # Roadmap
    rm = parse_roadmap(roadmap_body or "")
    rl = []
    if not roadmap_body:
        rl.append("- roadmap unavailable (gh failed); run: gh issue view 1 -R " + HUB_REPO)
    # First, not last: the size cap trims a section from its end, and this is the line the prime prompt says to start from.
    if rm["next_session"]:
        rl.append("Next session should: " + one_line(rm["next_session"], 380))
    arcs = [a for a in rm["arcs"] if a["open"] > 0] or rm["arcs"]
    if arcs:
        a = arcs[0]
        rl.append(f"Active arc: {one_line(a['head'], 130)} {a['counts']}")
        rl.extend(f"  - open: {one_line(t, 110)}" for t in a["items"][:3])
    n3 = rm["next3"]
    if n3:
        rl.append("Next 3: " + one_line(re.sub(r"^Next 3 sessions\s*:?\s*", "", n3[0]), 220))
        shown = {}
        cur = None
        for raw in n3[1:]:
            if raw.startswith("Session +"):
                cur = one_line(raw, 90)
                shown[cur] = []
            elif cur is not None and len(shown[cur]) < 2:
                shown[cur].append(one_line(raw, 100))
        for sess, items in shown.items():
            rl.append(f"- {sess}")
            rl.extend(f"    {it}" for it in items)
    sections.append(("Roadmap (the issue body has the rest: gh issue view 1 -R " + HUB_REPO + ")", rl))

    # Git, tidy, budget
    sections.append(("Budget this week", [budget or "budget report unavailable"]))
    gl = [one_line(x, 88) for x in (git_log or "").splitlines() if x.strip()][:10]
    sections.append(("~/repos, last 10 commits", [f"- {g}" for g in gl] or ["- git log unavailable"]))
    sections.append(("Tidy report (hub/scripts/tidy-report.sh; counts)", [f"- {x}" for x in tidy_summary(tidy_text or "")] or ["- clean"]))
    return sections


def render(sections, now, machine, cap):
    sections = [(t, list(ls)) for t, ls in sections]

    def text():
        out = [f"# Hub status, built {_stamp(now)} on {machine}",
               "Private: stays on this machine, never sent to OpenClaw. A script built it with no model; the sources are "
               "lane.sh, lane reports, the muse inbox, the roadmap issue, git, tidy-report and the budget report. "
               "Read this instead of the roadmap body; open a source only for the item you are working on. "
               "A lane report does not end its lane: lanes stay open until their context is full.", ""]
        for title, lines in sections:
            out.append(f"## {title}")
            out.extend(lines)
            out.append("")
        return clock12("\n".join(out).rstrip() + "\n")  # last pass: whatever a source wrote, the page has no 24-hour clock

    s = text()
    while len(s.encode()) > cap:
        for k in range(len(sections) - 1, -1, -1):
            if len(sections[k][1]) > 2:
                sections[k][1].pop()
                break
        else:
            for k in range(len(sections) - 1, -1, -1):
                if len(sections[k][1]) > 1:
                    sections[k][1].pop()
                    break
            else:
                s = s.encode()[:cap].decode(errors="ignore")
                break
        s = text()
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="write here (atomically) instead of stdout")
    ap.add_argument("--now", help="ISO time to stamp (tests)")
    ap.add_argument("--cap", type=int, default=CAP_DEFAULT)
    ap.add_argument("--roadmap-file")
    ap.add_argument("--lane-status-file")
    ap.add_argument("--lanes-dir", default=str(HOME / ".claude/state/lanes"))
    ap.add_argument("--inbox", default=os.environ.get("HUB_INBOX") or os.environ.get("MUSE_INBOX") or str(HOME / ".claude/state/hub-inbox.md"))
    ap.add_argument("--open-questions-file", help="openclaw_dispatch.py open output to use instead of reading live state")
    ap.add_argument("--git-repo", default=str(HOME / "repos"))
    ap.add_argument("--tidy-file", help="tidy-report output to use instead of running it (tests)")
    ap.add_argument("--budget-file", default=str(HOME / ".claude/state/hub-digest-budget.txt"))
    ap.add_argument("--report-hours", type=int, default=24)
    ap.add_argument("--strict", action="store_true",
                    help="treat the inputs as live state even with --roadmap-file (a live build is always strict)")
    ap.add_argument("--internship-file", help="internship_status.py --digest output to use instead of running it")
    ap.add_argument("--spend-file", help="lane_spend.py --json output to use instead of reading the live ledger")
    ap.add_argument("--alert-state", help="dispatch state .json to file the alert in (tests); default the live one")
    a = ap.parse_args(argv)

    now = datetime.fromisoformat(a.now) if a.now else datetime.now()
    try:
        machine = (HOME / ".config/afa/machine").read_text().strip() or "unknown"
    except OSError:
        machine = "unknown"
    body = Path(a.roadmap_file).read_text() if a.roadmap_file else _run(
        ["gh", "issue", "view", "1", "-R", HUB_REPO, "--json", "body", "-q", ".body"])
    status = Path(a.lane_status_file).read_text() if a.lane_status_file else _run(
        [str(HOME / ".claude/scripts/lane.sh"), "--status"])
    try:
        inbox = Path(a.inbox).read_text(errors="replace")
    except OSError:
        inbox = ""
    git_log = _run(["git", "-C", a.git_repo, "log", "-10", "--format=%h %ad %s", "--date=" + GIT_DATE])
    tidy = Path(a.tidy_file).read_text() if a.tidy_file else _run(
        ["bash", str(HOME / "repos/hub/scripts/tidy-report.sh")], timeout=30)
    budget = budget_line(a.budget_file, now.timestamp())
    open_questions_error = None
    if a.open_questions_file:
        try:
            open_questions_text = Path(a.open_questions_file).read_text(errors="replace")
        except OSError as exc:
            open_questions_text, open_questions_error = "", "could not read open-questions file: %s" % exc
    else:
        open_questions_text, open_questions_error = dispatcher_open_questions()

    spend = None
    if a.spend_file:
        try:
            spend = json.loads(Path(a.spend_file).read_text())
        except (OSError, ValueError) as exc:
            spend = "could not read the spend file: %s" % type(exc).__name__
    elif not a.roadmap_file:
        raw = _run([sys.executable, str(Path(__file__).resolve().parent / "lane_spend.py"), "--json"], timeout=90)
        try:
            spend = json.loads(raw)
        except ValueError:
            spend = "lane_spend.py gave no readable answer"
    internship = None
    if a.internship_file:
        try:
            internship = [l for l in Path(a.internship_file).read_text(errors="replace").splitlines() if l.strip()]
        except OSError:
            internship = None
    elif not a.roadmap_file:
        internship = internship_lines()
    secs = build_sections(roadmap_body=body, lane_status=status, lanes_dir=a.lanes_dir, inbox_text=inbox,
                          git_log=git_log, tidy_text=tidy, budget=budget, now=now, report_hours=a.report_hours,
                          open_questions_text=open_questions_text, open_questions_error=open_questions_error, spend=spend, internship=internship)
    page = render(secs, now, machine, a.cap)
    if a.strict or not a.roadmap_file:
        problems = live_problems(body, page)
        if problems:
            sys.stderr.write("hub_digest: the live state does not parse: %s. Not writing the page; the previous one stays.\n" % "; ".join(problems))
            try:
                alert_hub("hub_digest", problems, "hub_digest.py", state_path=a.alert_state)
            except Exception as exc:  # the failure is already on stderr; a failed alert must not hide it
                sys.stderr.write("hub_digest: could not put the alert on the hub's list: %s\n" % exc)
            return 3
    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(page)
        tmp.chmod(0o600)
        tmp.replace(p)
    else:
        sys.stdout.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
