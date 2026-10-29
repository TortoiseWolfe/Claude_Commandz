#!/usr/bin/env python3
"""The mechanical half of /session-prime: gather the facts since the last comment, and post.

The model writes only three fields of the roadmap body (active arc, next 3 sessions, "next session
should") and, optionally, a short note. Everything else is done here, by script:

  session_prime_facts.py [--repo O/R] [--dir D]
        Print the facts since the rolling issue's last comment (commits, PRs and issues opened or
        closed, and for the private hub: lanes closed with each report's first line, inbox items
        ticked, tidy counts), the drift check and the stamp. Saves the issue's updatedAt (READ_AT) to
        ~/.claude/state/session-prime/ so the post can tell if anyone edited the issue meanwhile.

  session_prime_facts.py --post [--arc F] [--next3 F] [--next-should F] [--next-should-machine F] [--note F] [--session ID] [--dry-run]
        Replace those fields in the body, refresh the roadmap-stamp line, and (unless --dry-run) check
        updatedAt still equals READ_AT, write the body, append the session-end comment (heading, facts,
        the note, the next-session line), seed the drift cache and run the drift check.
        --dry-run prints the comment and what would change in the body, and never posts or writes.

A field file holds the whole replacement block, including its wrapper: a <details> block whose
<summary> starts "Arc N" or "Next 3 sessions", or a "### Active arc" / "### Next 3 sessions queued"
section. --next-should holds just the paragraphs (the heading is kept). A field that cannot be found
in the body is an error, never a guess. --next-should-machine holds this machine's own line, written under
"### Next session should (MACHINE)" (replaced, or added after the roadmap's own section); any machine may write
its own, none may write NX-01's. Lanes, inbox and tidy facts appear only for the private workspace hub: a public repo's comment must never carry lane reports or client lines.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hub_digest import alert_hub, one_line, report_first_line, tidy_summary  # noqa: E402

HUB_REPO = "TortoiseWolfe/workspace"
STAMP = re.compile(r"<!-- roadmap-stamp v1 [^>]*-->")
MAX_LANES = 15


class Fail(Exception):
    def __init__(self, msg, code=2):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------------------------------------
# Body editing
# ---------------------------------------------------------------------------------------------


def _details_span(lines, summary_rx):
    """(start, end) line indexes of the <details> block whose opening <summary> line matches, end exclusive."""
    for i, ln in enumerate(lines):
        if re.search(summary_rx, ln) and ("<details" in ln or (i > 0 and lines[i - 1].strip() == "<details>")):
            start = i - 1 if lines[i - 1].strip() == "<details>" and "<details" not in ln else i
            depth = 0
            for j in range(start, len(lines)):
                depth += len(re.findall(r"<details\b", lines[j])) - len(re.findall(r"</details>", lines[j]))
                if depth <= 0:
                    return start, j + 1
            raise Fail(f"unbalanced <details> from line {start + 1}")
    return None


def _section_span(lines, heading_rx):
    """(start, end) for a ### section: from its heading to the line before the next ---, ## or ### line."""
    for i, ln in enumerate(lines):
        if re.match(heading_rx, ln):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"(---\s*$|##\s)", lines[j]) or re.match(r"###\s", lines[j]):
                    end = j
                    break
            return i, end
    return None


NEXT_RX = r"###\s+Next session should\s*$"  # the roadmap's own line (NX-01's); "### Next session should (NCC-74656)" is a machine's line


def _check_new(new):
    if new.count("<details") != new.count("</details>"):
        raise Fail("the replacement block has unbalanced <details> tags")


def replace_next_machine(body, machine, new):
    """Put a machine's own next-session line into the body: replace its "### Next session should (MACHINE)" section, or add one right
    after the roadmap's own "### Next session should" section. Nothing else in the body moves."""
    if not re.fullmatch(r"[\w.-]+", machine or ""):
        raise Fail("machine name %r cannot be used in a heading" % machine)
    if machine == "NX-01":
        raise Fail("NX-01's next-session line is the roadmap's own: use --next-should")
    for ln in new.strip("\n").split("\n"):
        if re.match(r"\s*(#{1,6}\s|---|<details|</details)", ln):
            raise Fail("a machine's next-session line is plain paragraphs: it may not hold a heading, a --- rule or a <details> tag (%r)" % ln[:40])
    lines = body.split("\n")
    block = ["### Next session should (%s)" % machine, ""] + new.strip("\n").split("\n") + [""]
    span = _section_span(lines, r"###\s+Next session should \(%s\)\s*$" % re.escape(machine))
    if span:
        s, e = span
        return "\n".join(lines[:s] + block + lines[e:])
    main = _section_span(lines, NEXT_RX)
    if not main:
        raise Fail('no "### Next session should" section in the body to put the machine line after')
    return "\n".join(lines[:main[1]] + block + lines[main[1]:])


def replace_field(body, field, new):
    """Return body with one field replaced. field: arc | next3 | next-should. Raises Fail when not found."""
    lines = body.split("\n")
    new = new.strip("\n")
    first = new.split("\n", 1)[0]
    if field == "next-should":
        span = _section_span(lines, NEXT_RX)
        if not span:
            raise Fail('no "### Next session should" section in the body')
        s, e = span
        block = ["### Next session should", ""] + new.split("\n") + [""]
        return "\n".join(lines[:s] + block + lines[e:])
    _check_new(new)
    if first.lstrip().startswith("<details"):
        if field == "arc":
            m = re.search(r"<summary><b>(Arc \d+)\b", new)
            if not m:
                raise Fail('the arc block\'s <summary> must start with <b>Arc N')
            rx = rf"<summary><b>{re.escape(m.group(1))}\b"
        else:
            if not re.search(r"<summary><b>Next 3 sessions", new):
                raise Fail('the next-3 block\'s <summary> must start with <b>Next 3 sessions')
            rx = r"<summary><b>Next 3 sessions"
        span = _details_span(lines, rx)
    else:
        rx = r"###\s+Active arc\b" if field == "arc" else r"###\s+Next 3 sessions\b"
        if not re.match(rx, first):
            raise Fail(f"the {field} block must start with a <details> block or its ### heading")
        span = _section_span(lines, rx)
    if not span:
        raise Fail(f"could not find the existing {field} block in the body (looked for {rx})")
    s, e = span
    return "\n".join(lines[:s] + new.split("\n") + lines[e:])


def body_problems(body):
    """What stops this script editing the live roadmap body, or [] when it still can. The body is the one place these edits land, so
    before anything is posted its three fields must be found and replaced by themselves without changing a byte (an identity edit).
    These were the asserts of a test that ran only against the real body on one machine; they run here on every facts step instead."""
    if not (body or "").strip():
        return ["the roadmap issue body came back empty"]
    lines = body.split("\n")
    probs = []
    for field, find, name in (("arc", lambda: _details_span(lines, r"<summary><b>Arc \d+\b"), "an Arc <details> block"),
                              ("next3", lambda: _details_span(lines, r"<summary><b>Next 3 sessions"), 'a "Next 3 sessions" <details> block'),
                              ("next-should", lambda: _section_span(lines, NEXT_RX), 'a "### Next session should" section')):
        try:
            span = find()
            if not span:
                probs.append("no %s in the body" % name)
                continue
            s, e = span
            text = "\n".join(lines[s:e] if field != "next-should" else lines[s + 1:e]).strip("\n")
            if replace_field(body, field, text) != body:
                probs.append("replacing %s with itself changed the body" % name)
        except Fail as exc:
            probs.append("%s: %s" % (name, exc))
    return probs


def refresh_stamp(body, sha, branch, generated, issue):
    stamp = f"<!-- roadmap-stamp v1 sha={sha} branch={branch} generated={generated} issue=#{issue} -->"
    if STAMP.search(body):
        return STAMP.sub(lambda _: stamp, body, count=1)
    return body.rstrip("\n") + "\n\n" + stamp + "\n"


# ---------------------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------------------


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ticked_since(inbox_text, since):
    """-> [(title, id)] for [x] items whose '  - done YYYY-MM-DD[ HH:MM]' line is on or after `since` (local time)."""
    since_local = since.astimezone()
    out, cur = [], None
    for line in inbox_text.splitlines():
        m = re.match(r"- \[x\] [\d-]+ [\d:]+ \| ([^|]*?) \|(?: [^|]*?\|)? ?(\S*)\s*$", line)
        if m:
            cur = (m.group(1).strip(), m.group(2))
            continue
        d = re.match(r"\s+- done (\d{4}-\d\d-\d\d)(?: (\d\d:\d\d))?", line)
        if d and cur:
            if d.group(2):
                when = datetime.fromisoformat(f"{d.group(1)}T{d.group(2)}").astimezone()
                ok = when >= since_local
            else:
                ok = d.group(1) >= since_local.strftime("%Y-%m-%d")
            if ok:
                out.append(cur)
            cur = None
    return out


def open_lane_names():
    override = os.environ.get("SESSION_PRIME_OPEN_LANES")
    if override is not None:
        return {n for n in override.split(",") if n}
    script = HOME / ".claude/scripts/lane.sh"
    if not script.exists():
        return None
    try:
        r = subprocess.run([str(script), "--status"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        out = set()
        for line in r.stdout.splitlines():
            m = re.match(r"^([A-Za-z0-9._-]+)\s+report:", line)
            if m:
                out.add(m.group(1))
        return out
    except (OSError, subprocess.TimeoutExpired):
        return None


def lanes_reported(lanes_dir, since, open_names=None):
    if open_names is None:
        open_names = open_lane_names()
    out = []
    for p in Path(lanes_dir).glob("*/report.md"):
        try:
            mt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mt >= since:
            name = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", p.parent.name)
            if open_names is None:
                state = "unknown"
            else:
                state = "open" if name in open_names else "closed"
            out.append((mt, name, report_first_line(p, 150), state))
    out.sort(key=lambda x: x[0])
    return [(n, f, s) for _, n, f, s in out]


def window_changes(items, since, skip=None):
    """items: gh json rows. -> (opened [(num, title)], closed [(num, title, merged)])."""
    opened, closed = [], []
    for it in items:
        if it["number"] == skip:
            continue
        if parse_ts(it["createdAt"]) >= since:
            opened.append((it["number"], it["title"]))
        end = it.get("mergedAt") or it.get("closedAt")
        if end and parse_ts(end) >= since:
            closed.append((it["number"], it["title"], bool(it.get("mergedAt"))))
    return opened, closed


def comment_facts(f):
    """The facts part of the session-end comment, in the Appendix B-1 shape; sections with nothing drop out."""
    out = []
    if f["commits"]:
        n = len(f["commits"])
        where = f"all on `origin/{f['branch']}`" if not f["unpushed"] else f"{f['unpushed']} not yet pushed to `origin/{f['branch']}`"
        out.append(f"**Shipped ({n} commit{'s' if n != 1 else ''}, {where}):**")
        for full, short, subj in f["commits"][:20]:
            out.append(f"- [`{short}`](https://github.com/{f['repo']}/commit/{full}) — {one_line(subj, 110)}")
        if n > 20:
            out.append(f"- and {n - 20} more")
        out.append("")
    def tag(kind, n, t, merged=False):
        return f"{'PR ' if kind == 'pr' else ''}#{n} ({one_line(t, 70)}{', merged' if merged else ''})"
    closed = [tag("issue", n, t) for n, t, _ in f["issues_closed"]] + [tag("pr", n, t, m) for n, t, m in f["prs_closed"]]
    opened = [tag("issue", n, t) for n, t in f["issues_opened"]] + [tag("pr", n, t) for n, t in f["prs_opened"]]
    if closed:
        out += ["**Closed:** " + ", ".join(closed), ""]
    if opened:
        out += ["**Opened:** " + ", ".join(opened), ""]
    if f.get("lanes"):
        n = len(f["lanes"])
        out.append(f"**Lanes reported ({n}, first line of each report; a lane stays open until Jonathan closes it):**")
        out += [f"- {name} ({state}): {first or 'report written'}" for name, first, state in f["lanes"][:MAX_LANES]]
        if n > MAX_LANES:
            out.append(f"- and {n - MAX_LANES} more")
        out.append("")
    if f.get("inbox"):
        n = len(f["inbox"])
        out.append(f"**Inbox items ticked ({n}):**")
        out += [f"- {one_line(t, 100)}" for t, _ in f["inbox"][:12]]
        if n > 12:
            out.append(f"- and {n - 12} more")
        out.append("")
    return "\n".join(out).rstrip("\n")


def render_comment(f, now, machine, where, session, note, next_should, next_machine=""):
    ses = f", session {session}" if session else ""
    head = f"## Session ended {now.strftime('%Y-%m-%d %-I:%M %p %Z')} ({machine}, {where}{ses})"
    parts = [head, "", comment_facts(f) or "_Nothing shipped, opened or closed since the last comment._"]
    if note and note.strip():
        parts += ["", note.strip()]
    if next_should and next_should.strip():
        parts += ["", "**Next session should:** " + next_should.strip().split("\n\n")[0].replace("\n", " ")]
    if next_machine and next_machine.strip():
        parts += ["", "**Next session should (%s):** " % machine + next_machine.strip().split("\n\n")[0].replace("\n", " ")]
    return "\n".join(parts).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------------------------
# gh, git, state
# ---------------------------------------------------------------------------------------------


def sh(cmd, cwd=None, timeout=60, check=True):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        if check:
            raise Fail(f"{cmd[0]} failed: {e}", 3)
        return ""
    if r.returncode != 0 and check:
        raise Fail(f"{' '.join(cmd[:4])} failed: {r.stderr.strip()[:300]}", 3)
    return r.stdout


GH_TRIES = 3
GH_RETRY_SLEEP = float(os.environ.get("PRIME_GH_RETRY_SLEEP", "2"))


def gh_json(args, cwd=None):
    """A read-only gh call, parsed. GitHub's GraphQL endpoint answers 502/504 or an empty body now and then (26 Oct: 6 of 7 tries on the
    248-comment hub roadmap), so a failed or unparsable read is tried again before it is an error. Writes (issue edit/comment) never come here."""
    for n in range(GH_TRIES):
        try:
            return json.loads(sh(["gh"] + args, cwd=cwd) or "null")
        except (Fail, ValueError):
            if n == GH_TRIES - 1:
                raise
            time.sleep(GH_RETRY_SLEEP * (n + 1))


def rolling_issue(repo, num, cwd=None):
    """-> (body, updatedAt, createdAt, createdAt of the last comment or None), all from REST. `gh issue view --json comments` pulls every
    comment body through GraphQL and kept failing on the 248-comment hub roadmap; REST gives the issue's own fields and, with per_page=1
    and page=<comment count>, only the last comment."""
    path = f"repos/{repo}/issues/{num}"
    for _ in range(3):
        iss = gh_json(["api", path], cwd)
        n = int(iss.get("comments") or 0)
        if not n:
            return iss.get("body") or "", iss["updated_at"], iss["created_at"], None
        page = gh_json(["api", f"{path}/comments?per_page=1&page={n}"], cwd)
        if page:
            return iss.get("body") or "", iss["updated_at"], iss["created_at"], page[-1]["created_at"]
        # a comment was deleted between the two reads: the count is stale, read the issue again
    raise Fail(f"{repo}#{num}: the comment count kept changing while reading the last comment", 3)


def tip_info(cwd):
    ref = sh(["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], cwd=cwd, check=False).strip()
    branch = re.sub(r"^origin/", "", ref) or "main"
    tip = f"origin/{branch}"
    r = subprocess.run(["git", "merge-base", "--is-ancestor", tip, "HEAD"], cwd=cwd, capture_output=True)
    if r.returncode == 0:
        tip = "HEAD"
    return branch, tip, sh(["git", "rev-parse", tip], cwd=cwd).strip()


def gather(args, now):
    cwd = args.dir
    repo = args.repo or sh(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd=cwd).strip()
    machine = args.machine or _read(HOME / ".config/afa/machine").strip()
    issues = gh_json(["issue", "list", "--repo", repo, "--state", "open", "--label", "next-session", "--json", "number,title"], cwd)
    if not issues:
        raise Fail(f"no open next-session issue in {repo}: seed one (session-prime.md, Seed)", 4)
    if len(issues) > 1:
        raise Fail(f"more than one open next-session issue in {repo}: ask the user which is canonical", 5)
    num = issues[0]["number"]
    body, updated_at, created_at, last_comment = rolling_issue(repo, num, cwd)
    last = last_comment or created_at
    since = parse_ts(last)
    day = since.strftime("%Y-%m-%d")

    branch, tip, tip_sha = tip_info(cwd)
    log = sh(["git", "log", tip, f"--since={last}", "--format=%H%x09%h%x09%s"], cwd=cwd, check=False)
    commits = [tuple(x.split("\t", 2)) for x in log.splitlines() if x.count("\t") >= 2]
    unpushed = 0
    if tip == "HEAD":
        ahead = sh(["git", "rev-list", "--count", f"origin/{branch}..HEAD"], cwd=cwd, check=False).strip()
        unpushed = int(ahead) if ahead.isdigit() else 0

    q = ["--repo", repo, "--state", "all", "--search", f"updated:>={day}", "--limit", "100"]
    iss_rows = gh_json(["issue", "list"] + q + ["--json", "number,title,createdAt,closedAt"], cwd) or []
    pr_rows = gh_json(["pr", "list"] + q + ["--json", "number,title,createdAt,closedAt,mergedAt"], cwd) or []
    io, ic = window_changes(iss_rows, since, skip=num)
    po, pc = window_changes(pr_rows, since)

    private_hub = False
    if repo == HUB_REPO:
        private_hub = bool(gh_json(["repo", "view", repo, "--json", "isPrivate"], cwd).get("isPrivate"))
    f = dict(repo=repo, branch=branch, commits=commits, unpushed=unpushed, issues_opened=io, issues_closed=[(n, t, m) for n, t, m in ic],
             prs_opened=po, prs_closed=pc, lanes=[], inbox=[], tidy=[])
    if private_hub:
        f["lanes"] = lanes_reported(args.lanes_dir, since)
        f["inbox"] = ticked_since(inbox_text(args, since), since)
        f["tidy"] = tidy_summary(sh(["bash", str(HOME / "repos/hub/scripts/tidy-report.sh")], timeout=40, check=False))
    return dict(repo=repo, num=num, machine=machine, body=body, updated_at=updated_at, last_comment=last,
                branch=branch, tip_sha=tip_sha, facts=f, private_hub=private_hub)


def _read(p):
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return ""


def inbox_text(args, since):
    """The board plus the archive months it may have been moved into (hub_inbox_archive.py), so a done item is listed
    for the week it was ticked whichever file holds it now. Archive files are named YYYY-MM.md by the month of the last touch."""
    parts = []
    adir = Path(args.inbox_archive)
    if adir.is_dir():
        floor = since.astimezone().strftime("%Y-%m")
        parts = [_read(p) for p in sorted(adir.glob("????-??.md")) if p.stem >= floor]
    return "\n".join(parts + [_read(args.inbox)])


def state_path(args, repo):
    return Path(args.state_dir) / (repo.replace("/", "__") + ".json")


def drift(args, mode, extra=()):
    hook = Path(args.drift_hook)
    if not hook.exists():
        return "(drift hook not installed)"
    r = subprocess.run(["bash", str(hook), mode, *extra], cwd=args.dir, capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip() or f"(drift {mode}: no output)"


# ---------------------------------------------------------------------------------------------


UNATTENDED_FIXED = ("repo", "machine", "dir", "lanes_dir", "inbox", "inbox_archive", "state_dir", "drift_hook", "now", "alert_state")


def unattended_guard(ap, a):
    """An unattended lane (a dispatcher's oc- lane) is allowed this script by a narrow permission rule, so the flags that point it
    elsewhere or run another script (--drift-hook) are refused, and the text it posts may only come from the lane's own hand-off folder."""
    for k in UNATTENDED_FIXED:
        if getattr(a, k) != ap.get_default(k):
            raise Fail("an unattended lane may not set --%s" % k.replace("_", "-"), 2)
    hand = os.environ.get("AFA_LANE_HAND", "")
    for k in ("arc", "next3", "next_should", "next_should_machine", "note"):
        p = getattr(a, k)
        if p is None:
            continue
        root = os.path.realpath(hand) if hand else None
        real = os.path.realpath(p)
        if not root or os.path.commonpath([root, real]) != root:
            raise Fail("an unattended lane may post --%s only from its own hand-off folder (AFA_LANE_HAND)" % k.replace("_", "-"), 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--dir", default=os.getcwd())
    ap.add_argument("--machine")
    ap.add_argument("--post", action="store_true", help="edit the body and append the comment")
    ap.add_argument("--dry-run", action="store_true", help="print what would be posted; never post or write")
    ap.add_argument("--arc")
    ap.add_argument("--next3")
    ap.add_argument("--next-should")
    ap.add_argument("--next-should-machine", help="file: this machine's own next-session line (any machine; the tower writes this one)")
    ap.add_argument("--note", help="file: extra comment sections (Roadmap changes, Watch)")
    ap.add_argument("--session", default=os.environ.get("CLAUDE_SESSION_ID", ""))
    ap.add_argument("--lanes-dir", default=str(HOME / ".claude/state/lanes"))
    ap.add_argument("--inbox", default=os.environ.get("HUB_INBOX") or os.environ.get("MUSE_INBOX") or str(HOME / ".claude/state/hub-inbox.md"))
    ap.add_argument("--inbox-archive", default=str(HOME / ".claude/state/hub-inbox-archive"))
    ap.add_argument("--state-dir", default=str(HOME / ".claude/state/session-prime"))
    ap.add_argument("--drift-hook", default=str(HOME / ".claude/hooks/roadmap-drift.sh"))
    ap.add_argument("--now", help="ISO time (tests)")
    ap.add_argument("--alert-state", help="dispatch state .json to file a live-body alert in (tests); default the live one")
    a = ap.parse_args(argv)
    try:
        if os.environ.get("AFA_LANE_UNATTENDED") == "1":
            unattended_guard(ap, a)
        return run(a)
    except Fail as e:
        print(f"session_prime_facts: {e}", file=sys.stderr)
        return e.code


def run(a):
    now = datetime.fromisoformat(a.now).astimezone() if a.now else datetime.now().astimezone()
    g = gather(a, now)
    f = g["facts"]
    where = str(Path(a.dir).resolve()).replace(str(HOME), "~")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fields = {k: _fieldfile(getattr(a, k.replace("-", "_"))) for k in ("arc", "next3", "next-should")}
    next_machine = _fieldfile(a.next_should_machine)
    note = _fieldfile(a.note)

    if not a.post:
        print(f"session-prime facts for {g['repo']}#{g['num']} (machine {g['machine'] or 'UNKNOWN'}, {where})")
        print(f"READ_AT {g['updated_at']}; facts since the last comment at {g['last_comment']}")
        print(f"stamp now: sha={g['tip_sha']} branch={g['branch']} generated={generated}")
        print(f"drift: {drift(a, 'check')}")
        if g["private_hub"]:
            print("tidy (hub backlog line, if any): " + ("; ".join(f["tidy"]) or "clean"))
        print("\n--- facts for the comment ---")
        print(comment_facts(f) or "(nothing since the last comment)")
        if not a.dry_run:
            sp = state_path(a, g["repo"])
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(json.dumps({"issue": g["num"], "read_at": g["updated_at"], "read_on": generated}))
        if g["repo"] == HUB_REPO:  # the hub roadmap is the live body every post edits: say so loudly, and wake the hub, when it stops parsing
            problems = body_problems(g["body"])
            if problems:
                print("\nsession_prime_facts: the live hub roadmap body no longer parses: %s. A post would fail or damage it." % "; ".join(problems),
                      file=sys.stderr)
                try:
                    alert_hub("session_prime_facts", problems, "session_prime_facts.py", state_path=a.alert_state)
                except Exception as exc:  # the failure is already on stderr; a failed alert must not hide it
                    print("session_prime_facts: could not put the alert on the hub's list: %s" % exc, file=sys.stderr)
                return 7
        return 0

    if not g["machine"] or g["machine"].upper() == "UNKNOWN":
        raise Fail("machine unknown: ask the user which machine this is")
    if g["repo"] == HUB_REPO and g["machine"] != "NX-01" and any(fields.values()):
        raise Fail("on the tower the hub roadmap's active arc, next 3 and next-session line belong to NX-01: "
                   "edit only your own lines by hand and post a note without the three fields")
    body = g["body"]
    changed = []
    if next_machine.strip():
        if g["repo"] != HUB_REPO:
            raise Fail("--next-should-machine belongs to the hub roadmap only")
        before = len(body)
        body = replace_next_machine(body, g["machine"], next_machine)
        changed.append("next-should (%s) (%+d bytes)" % (g["machine"], len(body) - before))
    for k, text in fields.items():
        if text:
            before = len(body)
            body = replace_field(body, k, text)
            changed.append(f"{k} ({len(body) - before:+d} bytes)")
    if changed and not (0.5 < len(body) / max(len(g["body"]), 1) < 2):
        raise Fail("the edited body changed size by more than half; refusing (a wrong span?)")
    body = refresh_stamp(body, g["tip_sha"], g["branch"], generated, g["num"])
    comment = render_comment(f, now, g["machine"], where, a.session, note, fields["next-should"], next_machine)

    if a.dry_run:
        print(f"DRY RUN for {g['repo']}#{g['num']}: nothing is posted or written.")
        print(f"body: replace {', '.join(changed) or 'no fields'}; stamp -> sha={g['tip_sha'][:12]} generated={generated}; "
              f"{len(g['body'])} -> {len(body)} bytes")
        try:
            st = json.loads(state_path(a, g["repo"]).read_text())
            guard = "ok" if st.get("read_at") == g["updated_at"] else f"WOULD REFUSE: issue changed since the facts were read ({st.get('read_at')})"
        except (OSError, ValueError):
            guard = "WOULD REFUSE: no saved READ_AT (run the facts step first)"
        print(f"updatedAt guard: {guard}")
        print("\n--- comment that would be posted ---")
        print(comment)
        return 0

    sp = state_path(a, g["repo"])
    try:
        st = json.loads(sp.read_text())
    except (OSError, ValueError):
        raise Fail("no saved READ_AT: run the facts step first (no --post), then edit the fields, then post")
    if st.get("issue") != g["num"] or st.get("read_at") != g["updated_at"]:
        raise Fail(f"{g['repo']}#{g['num']} changed since the facts were read (updatedAt {g['updated_at']}, read at "
                   f"{st.get('read_at')}); another machine or lane edited it. Re-read, redo the fields, run the facts again", 6)
    tmp = Path(a.state_dir) / "post-body.md"
    tmp.write_text(body)
    sh(["gh", "issue", "edit", str(g["num"]), "--repo", g["repo"], "--body-file", str(tmp)], cwd=a.dir)
    tmp.write_text(comment)
    sh(["gh", "issue", "comment", str(g["num"]), "--repo", g["repo"], "--body-file", str(tmp)], cwd=a.dir)
    tmp.unlink(missing_ok=True)
    sp.unlink(missing_ok=True)
    print(f"posted {g['repo']}#{g['num']}: body ({', '.join(changed) or 'stamp only'}) and a comment")
    print(drift(a, "seed-cache", (g["repo"], str(g["num"]), g["tip_sha"], g["branch"], generated)))
    print(drift(a, "check"))
    return 0


def _fieldfile(p):
    if not p:
        return ""
    try:
        return Path(p).read_text()
    except OSError as e:
        raise Fail(f"cannot read {p}: {e}")


if __name__ == "__main__":
    sys.exit(main())
