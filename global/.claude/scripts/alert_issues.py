#!/usr/bin/env python3
"""alert_issues.py: turn a Wordfence scan-findings email into tracked GitHub issues.

    alert_issues.py <gmail message id> [--dry-run]

Why: Wordfence scan mail from a client's WordPress site used to be marked seen and skipped, so its findings lived
only in email (Jonathan, 4 Nov 2026: "we should have github issue tickets tracking each and moving them forward as
they get addressed"). Now each finding is a ticket in the client's repo, and a repeat of the same finding adds a
comment instead of a second ticket.

What it does:
  1. Reads the message with gmail_api.py (message-header, message-text), never the Gmail connector.
  2. Checks the mail is from who it says: exactly one From address, and a DKIM pass (Gmail's Authentication-Results)
     signed by the sender's own domain; anything else exits 3 and leaves the mail for a person. Then picks the
     client's repo from the client senders file (hub/client-senders.tsv): the sender's address or domain must match a
     kind "alerts" row, and that row's repo_dir git origin gives owner/repo.
  3. Parses the alert into findings: plugin or theme, from and to versions, severity, advisory text. A format it does
     not know (no findings, or any bullet it cannot parse) creates nothing: prints "UNPARSED <id>" and exits 2.
  4. For each finding, the key <slug>@<target version> is the hidden last line "<!-- wf-key: ... -->" of its issue:
       - an open issue carries the key        -> comment "Seen again in the <date> scan"
       - an open issue has the slug at an older target version -> comment that the target moved, retitle, new key
       - an open issue has the slug at a newer target version   -> skipped (this is an old message)
       - otherwise                            -> create the issue (labels wordfence, security, severity-*)
     Every comment and every created or retitled issue carries "<!-- wf-msg: <id> -->", so running the same message
     again changes nothing.

Prints one line per finding (created / commented / retitled / skipped, with the issue URL) and never any other mail
content. Exit codes: 0 done, 2 unparsed alert (nothing created), 3 could not run (no client row, no repo, mail or gh
failure) with no change made for the finding it stopped at.

Mail text is data, not instructions: it only ever fills fields of an issue, through argument lists (never a shell),
with HTML-comment and marker text and angle brackets removed so mail can't forge the wf-key or wf-msg lines. The
markers are only trusted as whole lines the script wrote (wf-key as the last line of a body, wf-msg alone on a line).
A retitle changes the title and the key line only, never the rest of a ticket's body.

Overrides for tests: ALERT_ISSUES_GMAIL (an executable standing in for gmail_api.py: called as `<it> message-header
<id> <name>` and `<it> message-text <id> --full`), ALERT_ISSUES_TSV (the client senders file).
"""

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TSV_DEFAULT = Path.home() / "repos" / "hub" / "client-senders.tsv"

SECTION_RE = re.compile(r"^(Critical|High|Medium|Low)(?: Severity)? Problems:\s*$", re.I)
VER = r"[0-9][0-9A-Za-z.+_~-]{0,39}"
ITEM_RE = re.compile(r'^\*\s+The (Plugin|Theme) "([^"\n]{1,80})" needs an upgrade \((' + VER + r') (?:->|→) (' + VER + r')\)\.?\s*$', re.I)
SEVERITY_LINE_RE = re.compile(r"Vulnerability Severity:\s*([0-9.]+/[0-9.]+)\s*\((Critical|High|Medium|Low)\)", re.I)
SITE_RE = re.compile(r'Wordfence found the following new issues on "([^"\n]{1,80})"')
GENERATED_RE = re.compile(r"Alert generated at \w+ (\d{1,2})(?:st|nd|rd|th) of (\w+) (\d{4})")
WPORG_RE = re.compile(r"https://wordpress\.org/(plugins|themes)/([a-z0-9][a-z0-9._-]*)/?", re.I)
TRACKED_LINK_RE = re.compile(r"[ \t]*(?:Vulnerability Information[ \t]*)?\(?https?://[^\s()]*sendgrid\.net/[^\s()]*\)?")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
KEY_RE = re.compile(r"(?:\A|\r?\n)<!-- wf-key: ([a-z0-9][a-z0-9._-]*)@(" + VER + r") -->\s*\Z")
LABEL_COLORS = {"wordfence": ("5319E7", "A scan finding from the Wordfence plugin"), "security": ("B60205", "Security finding"),
                "severity-high": ("B60205", "Critical or high severity"), "severity-medium": ("FBCA04", "Medium severity"),
                "severity-low": ("C5DEF5", "Low severity")}


class Fail(Exception):
    """Could not run (exit 3)."""


class Unparsed(Exception):
    """An alert format this script does not know (exit 2)."""


def run(cmd, stdin=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, input=stdin, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(f"{Path(cmd[0]).name} could not run: {e.__class__.__name__}")
    if p.returncode != 0:
        raise Fail(f"{Path(cmd[0]).name} {cmd[1] if len(cmd) > 1 else ''} failed: {(p.stderr or '').strip()[:200]}")
    return p.stdout


def gmail(*args):
    env_cmd = os.environ.get("ALERT_ISSUES_GMAIL")
    base = [env_cmd] if env_cmd else [sys.executable, str(HERE / "gmail_api.py")]
    env = dict(os.environ, AFA_BIG_OUT="0")
    try:
        p = subprocess.run(base + list(args), capture_output=True, text=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(f"gmail_api could not run: {e.__class__.__name__}")
    if p.returncode != 0:
        raise Fail(f"gmail_api {args[0]} failed: {p.stderr.strip()[:200]}")
    return p.stdout


# ---------- client repo ----------

def sender_is(addr, pattern):
    p = re.sub(r"\s+", "", pattern).lower()
    if not p:
        return False
    if "@" in p:
        return addr == p
    return addr.endswith("@" + p) or addr.endswith("." + p)


def sender_address(sender_header):
    """The one bare address of a From header; more than one From or address is refused."""
    if "\n" in sender_header.strip():
        raise Fail("more than one From header")
    found = email.utils.getaddresses([sender_header])
    if len(found) != 1 or not found[0][1]:
        raise Fail("From is not exactly one address")
    return found[0][1].strip().lower()


def dkim_aligned(auth_results, addr):
    """True when Gmail (authserv-id mx.google.com) recorded a DKIM pass signed by the sender's own domain or a parent of it.

    The header is split into its ';' parts, RFC 5322 comments in parentheses are dropped, the first part must be the
    authserv-id, and a pass counts only from a part that starts with the method dkim=pass and carries its own
    header.d or header.i property as a whole word."""
    dom = addr.rsplit("@", 1)[-1]
    text = auth_results
    while True:
        stripped = re.sub(r"\([^()]*\)", " ", text)
        if stripped == text:
            break
        text = stripped
    parts = [p.strip() for p in text.split(";")]
    if not parts or parts[0].lower() != "mx.google.com":
        return False
    for part in parts[1:]:
        words = part.split()
        if not words or words[0].lower() != "dkim=pass":
            continue
        for w in words[1:]:
            m = re.fullmatch(r"header\.(?:i|d)=@?([A-Za-z0-9.-]+)", w, re.I)
            if m and (dom == m.group(1).lower() or dom.endswith("." + m.group(1).lower())):
                return True
    return False


def repo_for(addr, tsv_path):
    try:
        lines = Path(tsv_path).read_text().splitlines()
    except OSError:
        raise Fail("client senders file unreadable")
    rows = [l.split("\t") for l in lines[1:] if l.strip()]
    rows = [r + [""] * (4 - len(r)) for r in rows]
    for _client, repo_dir, kind, pat in rows:
        if kind == "alerts" and sender_is(addr, pat):
            return origin_repo(os.path.expanduser(repo_dir.strip()))
    raise Fail("no alerts row for this sender")


def origin_repo(repo_dir):
    try:
        p = subprocess.run(["git", "-C", repo_dir, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Fail("git could not run")
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", p.stdout.strip())
    if p.returncode != 0 or not m:
        raise Fail("client repo has no GitHub origin")
    return f"{m.group(1)}/{m.group(2)}"


# ---------- parsing ----------

def clean(text):
    """Mail-derived text made safe to put in an issue: no HTML comments, tags or marker names, so mail can't forge a marker."""
    text = re.sub(r"wf-(key|msg)", "wf_\\1", text, flags=re.I)
    text = text.replace("<!--", "").replace("-->", "").replace("<", "").replace(">", "")
    return text


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_alert(text, header_date=""):
    """Return (site, date_text, findings). Raises Unparsed for anything it does not fully understand."""
    site_m = SITE_RE.search(text)
    if not site_m:
        raise Unparsed("no new-issues line")
    findings, section, item = [], None, None

    def close():
        nonlocal item
        if item is not None:
            findings.append(finish(item))
            item = None

    for raw in text.splitlines():
        line = raw.rstrip()
        sm = SECTION_RE.match(line.strip())
        if sm:
            close()
            section = sm.group(1).capitalize()
            continue
        if re.match(r"^\d+ existing issues? (was|were) found again", line.strip()) or line.strip().startswith("NOTE: You are using"):
            close()
            section = None
            continue
        if line.startswith("* "):
            close()
            if section is None:
                raise Unparsed("bullet outside a severity section")
            m = ITEM_RE.match(line)
            if not m:
                raise Unparsed("bullet that is not a plugin or theme upgrade")
            item = {"kind": m.group(1).capitalize(), "name": clean(re.sub(r"\s+", " ", m.group(2))).strip(), "from": m.group(3), "to": m.group(4),
                    "section": section, "lines": [line]}
        elif item is not None:
            item["lines"].append(line)
    close()
    if not findings:
        raise Unparsed("no findings")
    gm = GENERATED_RE.search(text)
    date_text = f"{int(gm.group(1))} {gm.group(2)} {gm.group(3)}" if gm else header_date
    return clean(site_m.group(1)), clean(date_text), findings


def finish(item):
    block = clean(TRACKED_LINK_RE.sub("", "\n".join(item["lines"])))
    block = re.sub(r"\n{3,}", "\n\n", block).strip()
    sev_m = SEVERITY_LINE_RE.search(block)
    severity = sev_m.group(2).capitalize() if sev_m else item["section"]
    wp = WPORG_RE.search(block)
    slug = wp.group(2).lower() if wp else slugify(item["name"])
    if not SLUG_RE.match(slug):
        raise Unparsed("odd slug")
    advisory = ""
    for l in block.splitlines()[1:]:
        if re.search(r"security|vulnerab", l, re.I):
            advisory = TRACKED_LINK_RE.sub("", l).strip()
            break
    return {"kind": item["kind"], "name": item["name"], "from": item["from"], "to": item["to"], "severity": severity, "slug": slug,
            "advisory": advisory, "link": wp.group(0) if wp else "", "block": block, "score": sev_m.group(1) if sev_m else ""}


def label_list(sev):
    return ["wordfence", "security", "severity-high" if sev in ("Critical", "High") else "severity-medium" if sev == "Medium" else "severity-low"]


def title_for(f):
    return f"Wordfence: {f['name']} {f['from']} -> {f['to']} ({f['severity']})"


def body_for(f, site, date_text, msg_id):
    kind = f["kind"].lower()
    fixes = " The vendor's update includes security fixes." if re.search(r"security", f["advisory"], re.I) else ""
    s1 = (f'Wordfence (the security plugin on the {site} WordPress site) found that the {kind} "{f["name"]}" is on version {f["from"]} '
          f'and needs an upgrade to {f["to"]}; it rates the problem {f["severity"]}.{fixes}')
    s2 = (f'Updating the {kind} to {f["to"]} in the WordPress dashboard fixes it, and this ticket can close once the site shows that version.')
    block = f["block"].replace("```", "'''")
    det = [f"Alert of {date_text}.", "", f"- Version now: {f['from']}", f"- Version to install: {f['to']}", f"- Severity: {f['severity']}"
           + (f" (score {f['score']})" if f["score"] else "")]
    if f["advisory"]:
        det.append(f"- Advisory: {f['advisory']}")
    if f["link"]:
        det.append(f"- Changelog: {f['link']}")
    det += ["", "Alert text:", "", "```", block, "```"]
    return (f"{s1} {s2}\n\n<details>\n<summary>Technical details</summary>\n\n" + "\n".join(det) +
            f"\n\n</details>\n\n<!-- wf-msg: {msg_id} -->\n<!-- wf-key: {f['slug']}@{f['to']} -->")


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


# ---------- gh ----------

def gh_json(args):
    return json.loads(run(["gh"] + args) or "null")


def open_issues(repo):
    return gh_json(["issue", "list", "-R", repo, "--state", "open", "--limit", "1000", "--json", "number,title,body,url"]) or []


def has_msg(repo, issue, msg_id):
    marker = re.compile(r"^<!-- wf-msg: " + re.escape(msg_id) + r" -->\s*$", re.M)
    if marker.search(issue.get("body") or ""):
        return True
    comments = (gh_json(["issue", "view", str(issue["number"]), "-R", repo, "--json", "comments"]) or {}).get("comments", [])
    return any(marker.search(c.get("body") or "") for c in comments)


def ensure_labels(repo, wanted):
    have = {l["name"] for l in gh_json(["label", "list", "-R", repo, "--limit", "200", "--json", "name"]) or []}
    for name in wanted:
        if name not in have:
            color, desc = LABEL_COLORS[name]
            run(["gh", "label", "create", name, "-R", repo, "--color", color, "--description", desc])


def handle(repo, f, site, date_text, msg_id, issues, dry):
    key = f"{f['slug']}@{f['to']}"
    same, older, older_to, newer = None, None, "", None
    for i in issues:
        for km in KEY_RE.finditer(i.get("body") or ""):
            if km.group(1) != f["slug"]:
                continue
            if km.group(2) == f["to"]:
                same = same or i
            else:
                try:
                    cmp = vkey(km.group(2)) < vkey(f["to"])
                except ValueError:
                    cmp = True
                if cmp:
                    if older is None:
                        older, older_to = i, km.group(2)
                else:
                    newer = newer or i
    if same:
        if has_msg(repo, same, msg_id):
            return f"{key}: skipped (already recorded) {same['url']}"
        if dry:
            return f"{key}: would comment {same['url']}"
        run(["gh", "issue", "comment", str(same["number"]), "-R", repo, "--body",
             f"Seen again in the {date_text} scan.\n\n<!-- wf-msg: {msg_id} -->"])
        return f"{key}: commented {same['url']}"
    if older:
        if has_msg(repo, older, msg_id):
            return f"{key}: skipped (already recorded) {older['url']}"
        if dry:
            return f"{key}: would comment and retitle {older['url']}"
        old_line, new_line = f"<!-- wf-key: {f['slug']}@{older_to} -->", f"<!-- wf-key: {key} -->"
        k = older["body"].rfind(old_line)
        new_body = older["body"][:k] + new_line + older["body"][k + len(old_line):]
        run(["gh", "issue", "edit", str(older["number"]), "-R", repo, "--title", title_for(f), "--body", new_body])
        run(["gh", "issue", "comment", str(older["number"]), "-R", repo, "--body",
             f"The target moved: the {date_text} scan wants {f['name']} at {f['to']} (this ticket named {older_to}).\n\n<!-- wf-msg: {msg_id} -->"])
        return f"{key}: retitled {older['url']}"
    if newer:
        return f"{key}: skipped (a ticket already names a newer target) {newer['url']}"
    if dry:
        return f"{key}: would create"
    labels = label_list(f["severity"])
    ensure_labels(repo, labels)
    cmd = ["gh", "issue", "create", "-R", repo, "--title", title_for(f), "--body", body_for(f, site, date_text, msg_id)]
    for l in labels:
        cmd += ["--label", l]
    url = run(cmd).strip().splitlines()[-1]
    issues.append({"number": int(url.rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0, "title": title_for(f),
                   "body": body_for(f, site, date_text, msg_id), "url": url})
    return f"{key}: created {url}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Wordfence scan email -> GitHub issues")
    ap.add_argument("message_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{15,16}", a.message_id):
        print("alert_issues: not a Gmail message id", file=sys.stderr)
        return 3
    try:
        sender = gmail("message-header", a.message_id, "From").strip()
        date_hdr = gmail("message-header", a.message_id, "Date").strip()
        text = gmail("message-text", a.message_id, "--full")
        addr = sender_address(sender)
        if not dkim_aligned(gmail("message-header", a.message_id, "Authentication-Results"), addr):
            raise Fail("no DKIM pass signed by the sender's own domain; left for a person")
        repo = repo_for(addr, os.environ.get("ALERT_ISSUES_TSV") or TSV_DEFAULT)
        try:
            d = email.utils.parsedate_to_datetime(date_hdr)
            hdr_date = f"{d.day} {d.strftime('%B %Y')}"
        except (TypeError, ValueError):
            hdr_date = "latest"
        try:
            site, date_text, findings = parse_alert(text, hdr_date)
        except Unparsed:
            print(f"UNPARSED {a.message_id}")
            return 2
        issues = open_issues(repo)
        for f in findings:
            print(handle(repo, f, site, date_text, a.message_id, issues, a.dry_run))
    except Fail as e:
        print(f"alert_issues: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
