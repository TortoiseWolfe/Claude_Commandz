#!/usr/bin/env python3
"""notes_check.py: check for new notes from Hatch and the second tower.

Read-only toward Gmail: lists drafts and messages matching note queries, drops IDs
already handled or marked Label_32 (agent-notes-cc-processed), and reports new notes.
Never labels, edits, or deletes anything in Gmail. Never prints a secret or token.

--sweep is the one exception: it deletes fleet-note DRAFTS that are handled (rules below), so the owner's
Drafts folder holds the owner's own drafts. Dry run by default; --apply deletes. See sweep().
"""

import argparse
import email.utils
import fcntl
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

try:
    import gmail_api
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gmail_api

DRAFT_QUERIES = [
    'subject:"MUSE>CC" newer_than:3d',
    'subject:"NCC-74656>NX-01" newer_than:3d',
    'subject:"Re: [CC>MUSE]" newer_than:3d',
    '"H-7CH" -subject:"[CC>MUSE]" newer_than:3d',
]

MAIL_QUERIES = [
    'subject:"[MUSE>CC]" newer_than:3d -in:draft -label:agent-notes-cc-processed',
    'subject:"NCC-74656>NX-01" newer_than:3d -in:draft -label:agent-notes-cc-processed',
    'subject:"Re: [CC>MUSE]" newer_than:3d -in:draft -label:agent-notes-cc-processed',
    '"H-7CH" -subject:"[CC>MUSE]" newer_than:3d -in:draft -label:agent-notes-cc-processed',
]

LABEL_CC_PROCESSED = "Label_32"


class Logger:
    def __init__(self, path):
        self.path = path
        if self.path:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            except OSError:
                pass

    def write(self, msg):
        if not self.path:
            return
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"[{now_str}] {msg}\n")
        except OSError:
            pass


def parse_date(date_str):
    if not date_str:
        return 0.0
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        return dt.timestamp()
    except Exception:
        return 0.0


def append_processed(path, item_id):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    need_newline = False
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                need_newline = True
    with open(path, "a", encoding="utf-8") as f:
        if need_newline:
            f.write("\n")
        f.write(f"{item_id}\n")


def get_draft_ids(query):
    results = []
    token = None
    while True:
        params = {"q": query, "maxResults": 100}
        if token:
            params["pageToken"] = token
        page = gmail_api._api_retry("GET", "/drafts", params)
        drafts = page.get("drafts") or []
        for d in drafts:
            did = d.get("id")
            mid = (d.get("message") or {}).get("id")
            if did:
                results.append((did, mid))
        token = page.get("nextPageToken")
        if not token:
            break
    return results


def get_message_ids(query):
    results = []
    token = None
    while True:
        params = {"q": query, "maxResults": 100}
        if token:
            params["pageToken"] = token
        page = gmail_api._api_retry("GET", "/messages", params)
        messages = page.get("messages") or []
        for m in messages:
            mid = m.get("id")
            if mid:
                results.append(mid)
        token = page.get("nextPageToken")
        if not token:
            break
    return results


def check_notes(processed_path, log):
    log.write("start notes_check run")

    # Step 1: Collect IDs and dedupe across queries
    candidates = []
    seen_draft_ids = set()
    seen_message_ids = set()

    for q in DRAFT_QUERIES:
        drafts = get_draft_ids(q)
        log.write(f"query drafts {q!r}: {len(drafts)} result(s)")
        for did, mid in drafts:
            if not mid:
                d_info = gmail_api._api_retry("GET", f"/drafts/{did}", {"format": "minimal"})
                mid = (d_info.get("message") or {}).get("id")
            if did in seen_draft_ids or (mid and mid in seen_message_ids):
                log.write(f"drop duplicate draft ID {did} (message {mid})")
                continue
            seen_draft_ids.add(did)
            if mid:
                seen_message_ids.add(mid)
            candidates.append({
                "kind": "draft",
                "draft_id": did,
                "message_id": mid,
            })

    for q in MAIL_QUERIES:
        messages = get_message_ids(q)
        log.write(f"query mail {q!r}: {len(messages)} result(s)")
        for mid in messages:
            if mid in seen_message_ids:
                log.write(f"drop duplicate message ID {mid}")
                continue
            seen_message_ids.add(mid)
            candidates.append({
                "kind": "mail",
                "draft_id": None,
                "message_id": mid,
            })

    # Step 2: Drop notes whose draft ID or message ID is in the processed file
    processed_ids = set()
    if os.path.exists(processed_path):
        with open(processed_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    processed_ids.add(stripped)

    remaining_after_step2 = []
    for c in candidates:
        did = c["draft_id"]
        mid = c["message_id"]
        if did and did in processed_ids:
            log.write(f"drop draft {did}: draft ID in processed file")
            continue
        if mid and mid in processed_ids:
            log.write(f"drop {c['kind']} {did or mid}: message ID {mid} in processed file")
            continue
        remaining_after_step2.append(c)

    # Step 3: Fetch metadata for remaining notes; drop if Label_32 present
    remaining_notes = []
    for c in remaining_after_step2:
        msg = gmail_api._api_retry("GET", f"/messages/{c['message_id']}", {
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "Date"],
        })
        label_ids = msg.get("labelIds") or []
        if LABEL_CC_PROCESSED in label_ids:
            id_to_append = c["draft_id"] if c["kind"] == "draft" else c["message_id"]
            append_processed(processed_path, id_to_append)
            processed_ids.add(id_to_append)
            log.write(f"drop {c['kind']} {id_to_append}: has {LABEL_CC_PROCESSED}; appended to {processed_path}")
            continue

        headers = (msg.get("payload") or {}).get("headers") or []
        h = {}
        for hdr in headers:
            name = (hdr.get("name") or "").lower()
            val = hdr.get("value") or ""
            h[name] = " ".join(val.split())

        from_val = h.get("from", "")
        subject_val = h.get("subject", "")
        date_val = h.get("date", "")
        dt = parse_date(date_val)
        note_id = c["draft_id"] if c["kind"] == "draft" else c["message_id"]
        remaining_notes.append({
            "id": note_id,
            "kind": c["kind"],
            "from": from_val,
            "subject": subject_val,
            "date": date_val,
            "dt": dt,
        })

    # Output to stdout: oldest first
    remaining_notes.sort(key=lambda x: x["dt"])
    if not remaining_notes:
        print("notes: none new")
    else:
        for n in remaining_notes:
            print(f"NOTE {n['id']} | {n['kind']} | {n['from']} | {n['subject']} | {n['date']}")

    log.write(f"finished notes_check run: {len(remaining_notes)} note(s) reported")


# ---------------------------------------------------------------------------------------------------------------
# --sweep: delete HANDLED fleet-note drafts (26 Oct: fleet notes buried the owner's real drafts).
#
# One shared "absorbed" signal (31 Oct): the Gmail label agent-notes/cc-processed (Label_32, searchable as
# label:agent-notes-cc-processed) on the note's message. The READER applies it (muse_poll_record.sh processed <id>, or
# this script's own label step below); the AUTHOR's machine deletes the draft once it is on and the draft is over 24 hours
# old. Hatch cannot label drafts (the connector refuses message calls on them), so Hatch's signal is Trash; a trashed
# draft is gone from the Drafts list and needs nothing here.
#
# Each machine sweeps only its own: the machine name comes from ~/.config/afa/machine (NX-01 is also "CC", the name the
# Hatch channel uses). A subject tag [SRC>DST] makes a draft ours to delete in two cases:
#   DST is us (we are the reader): deleted once the ID is in our processed file (or the message wears Label_32) AND the
#                 draft is over 2 hours old. [MUSE>CC], [NCC-74656>NX-01] on NX-01; [MUSE>NCC-74656] on the tower.
#   SRC is us (we are the author): deleted only once ABSORPTION IS VERIFIED (Label_32, or a later reply from the other
#                 side on the same topic) and the draft is over 24 hours old. Never deleted on age alone: an unabsorbed note
#                 stays and is counted as "unabsorbed older than 24 h" in the sweep output.
# Any other fleet tag (another machine's note, Hatch's to the tower) is kept and counted. The label step (apply mode)
# first gives Label_31 + Label_32 to every draft addressed to us whose ID is in our processed file, so the author sees it.
# "Re:" forms, the untagged H-7CH branch and the owner-only recipient rule are as before.
# Everything else (no fleet tag: his real mail, client drafts) is counted and never touched. Gmail has no Trash for drafts,
# so each draft's raw text is saved to ~/.claude/state/draft-sweep/YYYY-MM-DD.jsonl BEFORE it is deleted (the undo).
# ---------------------------------------------------------------------------------------------------------------

FLEET_NAMES = {"NX-01", "NCC-74656", "CC", "MUSE"}
MACHINE_NAMES = {"NX-01": {"NX-01", "CC"}, "NCC-74656": {"NCC-74656"}}
INBOUND_MIN_AGE = 2 * 3600
OUTBOUND_MIN_AGE = 24 * 3600
LABEL_AGENT_NOTES = "Label_31"
MACHINE_FILE = os.path.expanduser("~/.config/afa/machine")
SWEEP_DIR = os.path.expanduser("~/.claude/state/draft-sweep")
_REPLY_RE = re.compile(r"^\s*(re|fwd?|aw)\s*:", re.I)
_TAG_RE = re.compile(r"\[([A-Za-z0-9-]+)>([A-Za-z0-9-]+)\]")


def my_names(machine=None):
    """The tag names this machine answers to: {"NX-01", "CC"} on NX-01, {"NCC-74656"} on the tower."""
    if machine is None:
        try:
            with open(MACHINE_FILE, encoding="utf-8") as f:
                machine = f.read().strip()
        except OSError:
            machine = "NX-01"
    return set(MACHINE_NAMES.get(machine, {machine}))


def parties(subject):
    """(tag, src, dst, is_reply) for the first fleet tag in a subject, or None. A tag counts only if both ends are fleet names."""
    subj = subject or ""
    for m in _TAG_RE.finditer(subj):
        src, dst = m.group(1), m.group(2)
        if src in FLEET_NAMES and dst in FLEET_NAMES:
            return m.group(0), src, dst, bool(_REPLY_RE.match(subj))
    return None


def classify(subject, mine=None):
    """(kind, label) for a fleet subject that is ours, or (None, None). kind: inbound (we are the reader), outbound (we
    are the author). A "Re:" in front of an outbound tag is the other side's reply, so it is inbound and its label starts
    with "Re: ". A tag between two other parties is not ours: (None, None)."""
    mine = my_names() if mine is None else mine
    p = parties(subject)
    if not p:
        return None, None
    tag, src, dst, reply = p
    if dst in mine:
        return "inbound", tag
    if src in mine:
        return ("inbound", "Re: " + tag) if reply else ("outbound", tag)
    return None, None


def _topic(subject, tag):
    t = (subject or "").replace(tag.replace("Re: ", ""), " ")
    t = " ".join(t.split())
    while _REPLY_RE.match(t):
        t = _REPLY_RE.sub("", t, count=1).strip()
    t = re.sub(r'["\[\]()<>\\]', " ", t)
    return " ".join(t.split())[:80]


def _read_processed(path):
    ids = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            ids = {ln.strip() for ln in f if ln.strip()}
    return ids


def _msg_meta(m):
    h = {}
    for hdr in (m.get("payload") or {}).get("headers") or []:
        h[(hdr.get("name") or "").lower()] = " ".join((hdr.get("value") or "").split())
    return {
        "subject": h.get("subject", ""), "from": h.get("from", ""),
        "recipients": [a.lower() for k in ("to", "cc", "bcc") if h.get(k)
                       for _, a in email.utils.getaddresses([h[k]]) if a],
        "date": h.get("date", ""), "t": int(m.get("internalDate") or 0) / 1000.0,
        "labels": list(m.get("labelIds") or []),
    }


def _list_all_drafts():
    out, token = [], None
    while True:
        params = {"maxResults": 100}
        if token:
            params["pageToken"] = token
        page = gmail_api._api_retry("GET", "/drafts", params)
        for d in page.get("drafts") or []:
            if d.get("id"):
                out.append((d["id"], (d.get("message") or {}).get("id")))
        token = page.get("nextPageToken")
        if not token:
            return out


def _draft_meta(did):
    try:
        d = gmail_api._api_retry("GET", f"/drafts/{did}", {"format": "metadata",
                                                           "metadataHeaders": ["Subject", "From", "To", "Cc", "Bcc", "Date"]})
    except gmail_api.GmailError as e:
        if " 404 " in str(e):
            return None  # deleted while we ran
        raise
    m = d.get("message") or {}
    rec = _msg_meta(m)
    rec["draft_id"], rec["message_id"] = did, m.get("id")
    return rec


def _signed_hatch(did):
    d = gmail_api._api_retry("GET", f"/drafts/{did}", {"format": "full"})
    stack = [(d.get("message") or {}).get("payload") or {}]
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts") or [])
        data = (part.get("body") or {}).get("data")
        if data and part.get("mimeType", "").startswith("text/"):
            try:
                if "H-7CH" in gmail_api.b64u_decode(data).decode("utf-8", "replace"):
                    return True
            except Exception:
                pass
    return False


def _has_reply(rec, kind_label, cache, owner):
    """A later message about the same topic from the other side: a tagged inbound note, or any "Re:" subject."""
    topic = _topic(rec["subject"], kind_label)
    if not topic:
        return None
    if topic not in cache:
        found = []
        page = gmail_api._api_retry("GET", "/messages", {"q": f'subject:"{topic}"', "maxResults": 10})
        for m in (page.get("messages") or [])[:10]:
            if m.get("id") == rec["message_id"]:
                continue
            msg = gmail_api._api_retry("GET", f"/messages/{m['id']}", {
                "format": "metadata", "metadataHeaders": ["Subject", "From", "To", "Cc", "Bcc", "Date"]})
            found.append(_msg_meta(msg) | {"id": m["id"]})
        cache[topic] = found
    for f in cache[topic]:
        kind, _ = classify(f["subject"])
        # Only the owner's own mailbox identity counts (Hatch and the tower write as the owner): a stranger's "Re:" with
        # a matching subject must not retire one of our notes.
        sender = email.utils.parseaddr(f["from"])[1].lower()
        if sender == owner and f["t"] > rec["t"] and (kind == "inbound" or (kind is None and _REPLY_RE.match(f["subject"]))):
            return f["id"]
    return None


def decide(rec, processed, now, cache, owner, mine=None):
    """-> (action, category, reason). action: delete | keep | other (not a fleet draft; never touched).
    A "keep" whose category ends in "[unabsorbed]" is an outbound note of ours over 24 hours old with no verified absorption."""
    mine = my_names() if mine is None else mine
    kind, label = classify(rec["subject"], mine)
    age = now - rec["t"]
    ids = {rec["draft_id"], rec["message_id"]}
    handled = bool(ids & processed) or LABEL_CC_PROCESSED in rec["labels"]
    if kind is None and parties(rec["subject"]):
        tag = parties(rec["subject"])[0]
        return "keep", tag, "another party's note (not to or from this machine)"
    if kind is None:
        if ids & processed and age > INBOUND_MIN_AGE and _signed_hatch(rec["draft_id"]):
            kind, label = "inbound", "untagged H-7CH"
        else:
            return "other", None, "no fleet tag"
    outside = sorted({a for a in rec["recipients"] if a.lower() != owner})
    if outside:
        return "keep", label, "addressed outside the mailbox owner's own address: " + ", ".join(outside)
    if kind == "inbound":
        if age <= INBOUND_MIN_AGE:
            return "keep", label, "under 2 hours old"
        if handled:
            return "delete", label, "handled (ID in processed file or Label_32), over 2 hours old"
        return "keep", (label + " [unabsorbed]" if age > OUTBOUND_MIN_AGE else label), "not handled yet"
    unabsorbed = label + " [unabsorbed]" if age > OUTBOUND_MIN_AGE else label
    if age <= OUTBOUND_MIN_AGE:
        return "keep", label, "under 24 hours old"
    if LABEL_CC_PROCESSED in rec["labels"]:
        return "delete", label, "absorbed (carries Label_32), over 24 hours old"
    reply = _has_reply(rec, label, cache, owner)
    if reply:
        return "delete", label, f"answered (reply message {reply}), over 24 hours old"
    return "keep", unabsorbed, "absorption not verified (no Label_32, no reply): kept"


def label_absorbed(recs, processed, mine, apply):
    """The reader's half of the shared signal: give Label_31 + Label_32 to every draft addressed to us whose ID is in our
    processed file and that lacks Label_32. Updates rec["labels"] so the same pass sees it. Returns how many were labelled."""
    n = 0
    for r in recs:
        kind, _ = classify(r["subject"], mine)
        if kind != "inbound" or LABEL_CC_PROCESSED in r["labels"] or not ({r["draft_id"], r["message_id"]} & processed):
            continue
        if apply:
            gmail_api._api_retry_post(f"/messages/{r['message_id']}/modify",
                                      {"addLabelIds": [LABEL_AGENT_NOTES, LABEL_CC_PROCESSED]})
            r["labels"] = list(r["labels"]) + [x for x in (LABEL_AGENT_NOTES, LABEL_CC_PROCESSED) if x not in r["labels"]]
        n += 1
    return n


def _archive(did, mid, rec, sweep_dir, now):
    d = gmail_api._api_retry("GET", f"/drafts/{did}", {"format": "raw"})
    os.makedirs(sweep_dir, mode=0o700, exist_ok=True)
    os.chmod(sweep_dir, 0o700)  # the archive holds raw mail: owner-only
    path = os.path.join(sweep_dir, time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")
    line = json.dumps({"draft_id": did, "message_id": mid, "subject": rec["subject"], "date": rec["date"],
                       "raw": (d.get("message") or {}).get("raw", "")})
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def sweep(processed_path, log, apply=False, now=None, sweep_dir=None, out=None, owner=None, mine=None):
    out = out or sys.stdout
    # The mailbox owner is read from the account once per run, never written into this public repo. If the lookup
    # fails the GmailError ends the sweep before anything is listed or deleted.
    owner = (owner or gmail_api.api("GET", "/profile")["emailAddress"]).lower()
    now = time.time() if now is None else now
    sweep_dir = sweep_dir or SWEEP_DIR
    mine = my_names() if mine is None else mine
    paths = processed_path if isinstance(processed_path, (list, tuple)) else [processed_path]
    processed = set().union(*(_read_processed(x) for x in paths))
    recs = [r for r in (_draft_meta(did) for did, _ in _list_all_drafts()) if r]
    cache = {}
    labelled = label_absorbed(recs, processed, mine, apply)
    rows = [(r,) + decide(r, processed, now, cache, owner, mine) for r in recs]
    by_tag = {}
    for r, action, label, reason in rows:
        if action != "other":
            by_tag.setdefault(label, {"delete": 0, "keep": 0})[action] += 1
    others = sum(1 for row in rows if row[1] == "other")
    fleet = [row for row in rows if row[1] != "other"]
    dels = [row for row in fleet if row[1] == "delete"]
    deleted = 0
    if apply:
        for r, _, label, reason in dels:
            try:
                _archive(r["draft_id"], r["message_id"], r, sweep_dir, now)
            except (OSError, gmail_api.GmailError) as e:
                log.write(f"sweep: NOT deleting {r['draft_id']}: archive failed: {e}")
                continue
            try:
                gmail_api.draft_delete(r["draft_id"])
            except gmail_api.GmailError as e:
                if " 404 " not in str(e):
                    raise
            deleted += 1
            log.write(f"sweep: deleted {r['draft_id']} [{label}] {r['subject']!r}: {reason}")
    mode = "APPLIED" if apply else "DRY RUN"
    unabsorbed = sum(1 for row in fleet if row[1] == "keep" and row[2].endswith("[unabsorbed]"))
    print(f"draft sweep ({mode}): {len(recs)} drafts, {len(fleet)} fleet notes, {others} not fleet notes (never touched)", file=out)
    for label in sorted(by_tag):
        print(f"  {label}: {by_tag[label]['delete'] + by_tag[label]['keep']} (delete {by_tag[label]['delete']}, keep {by_tag[label]['keep']})", file=out)
    if fleet:
        oldest = min(fleet, key=lambda row: row[0]["t"])[0]
        print(f"  oldest fleet draft: {oldest['date'] or time.ctime(oldest['t'])}", file=out)
    verb = "deleted" if apply else "would delete"
    print(f"  {verb}: {deleted if apply else len(dels)}", file=out)
    for r, action, label, reason in fleet:
        if action == "keep":
            print(f"  keep {r['draft_id']} {label} {r['subject'][:70]!r}: {reason}", file=out)
    print(f"  not fleet notes remaining: {others}", file=out)
    print(f"  labelled absorbed this pass ({'applied' if apply else 'would label'}): {labelled}", file=out)
    print(f"  unabsorbed older than 24 h (ours): {unabsorbed}", file=out)
    log.write(f"sweep {mode}: {len(recs)} drafts, {len(fleet)} fleet, {len(dels)} to delete, {deleted} deleted, {others} other, "
              f"{labelled} labelled, {unabsorbed} unabsorbed over 24 h")
    return {"drafts": len(recs), "fleet": len(fleet), "deleted": deleted, "would_delete": len(dels), "other": others,
            "labelled": labelled, "unabsorbed_old": unabsorbed}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check for new notes from Hatch and the second tower.")
    parser.add_argument("--processed", action="append", default=None,
                        help="Path to a processed IDs file (repeat for several). Default: agent-notes-processed.txt and any peer-*processed*.txt in ~/.claude/state")
    parser.add_argument("--log", default=os.path.expanduser("~/.claude/state/notes-check.log"),
                        help="Path to log file")
    parser.add_argument("--sweep", action="store_true",
                        help="Delete handled fleet-note drafts (dry run unless --apply); prints counts, never note bodies")
    parser.add_argument("--apply", action="store_true", help="With --sweep: really delete")
    args = parser.parse_args(argv)
    if not args.processed:
        state = os.path.expanduser("~/.claude/state")
        args.processed = [os.path.join(state, "agent-notes-processed.txt")] + sorted(glob.glob(os.path.join(state, "peer-*processed*.txt")))
    log = Logger(args.log)
    try:
        if args.sweep:
            lock = None
            if args.apply:
                state_dir = os.path.expanduser("~/.claude/state")
                os.makedirs(state_dir, exist_ok=True)
                lock = open(os.path.join(state_dir, "draft-sweep.lock"), "w")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return 0  # another sweep is running
            sweep(args.processed, log, apply=args.apply)
            return 0
        check_notes(args.processed[0], log)
        return 0
    except gmail_api.GmailError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
