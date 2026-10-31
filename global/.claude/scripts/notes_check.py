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
# this script's own label step below). Hatch cannot label drafts (the connector refuses message calls on them), so Hatch's
# own signal is Trash; a trashed draft is gone from the Drafts list and needs nothing here.
#
# NX-01 owns the Gmail sweeps (the tower has no Gmail token), so this sweep covers EVERY fleet tag in EVERY direction:
# [MUSE>CC], [CC>MUSE], [NX-01>NCC-74656], [NCC-74656>NX-01], [NCC-74656>MUSE], [MUSE>NCC-74656] and any new [X>Y] pair
# between fleet names. Every draft is paged through (no newest-50 cap). The machine name comes from ~/.config/afa/machine
# (NX-01 is also "CC", the name the Hatch channel uses). For a subject tag [SRC>DST] the READER is DST (SRC in a "Re:" form,
# which the other side wrote):
#   READER is us (inbound): deleted once the ID is in our processed file (or the message wears Label_32) AND the draft is
#                 over 2 hours old.
#   READER is anyone else (our own outbound note, or another party's note between Hatch and the tower): deleted only once the
#                 READER'S ABSORPTION IS PROVEN AND the draft is over 24 hours old. Proof is any one of:
#                   1. Label_32 on the note;
#                   2. the draft or message ID in the reader's published read record (READER_RECORDS: the tower publishes
#                      its read record, draft IDs only, to the private hub repo; this machine reads its checkout);
#                   3. a later message from the reader on the same topic (a tagged note the reader wrote, or a "Re:");
#                   4. a later done-note from the reader that names the draft ID.
#                 Never deleted on age alone. A note with no proof stays and is counted as unabsorbed; the count by pair is
#                 written to draft-sweep-status.json for the hub digest (never shown to the mailbox owner).
# The label step (apply mode) first gives Label_31 + Label_32 to every draft addressed to us whose ID is in our processed
# file, so the author sees it. "Re:" forms, the untagged H-7CH branch and the owner-only recipient rule are as before.
# Everything else (no fleet tag: his real mail, client drafts) is counted and never touched. Gmail has no Trash for drafts,
# so each draft's raw text is saved to ~/.claude/state/draft-sweep/YYYY-MM-DD.jsonl BEFORE it is deleted (the undo).
# ---------------------------------------------------------------------------------------------------------------

FLEET_NAMES = {"NX-01", "NCC-74656", "CC", "MUSE"}
MACHINE_NAMES = {"NX-01": {"NX-01", "CC"}, "NCC-74656": {"NCC-74656"}}
# A tag name -> the party it belongs to ("CC" is NX-01 on the Hatch channel).
GROUP = {"NX-01": "NX-01", "CC": "NX-01", "NCC-74656": "NCC-74656", "MUSE": "MUSE"}
# Published read records, by reader. The tower publishes draft IDs only, after each poll, to the private hub repo; the hub repo
# checkout on this machine is the copy we read. Hatch (MUSE) keeps no record: its signal is Trash, and a trashed draft is gone.
READER_RECORDS = {"NCC-74656": os.path.expanduser("~/repos/hub/tower/peer-notes-processed.txt")}
STATUS_FILE = os.path.expanduser("~/.claude/state/draft-sweep-status.json")
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


def route(subject):
    """(tag, author, reader, reply) for a fleet subject, or None. "Re: [A>B]" is B's reply to A's note, so B wrote it and A reads it."""
    p = parties(subject)
    if not p:
        return None
    tag, src, dst, reply = p
    return (tag, dst, src, True) if reply else (tag, src, dst, False)


def classify(subject, mine=None):
    """(kind, label) for a fleet subject that is ours, or (None, None). kind: inbound (we are the reader), outbound (we
    are the author). A "Re:" in front of a tag is the reply of the tag's DST to its SRC, so the reader is the SRC and the
    label starts with "Re: ". A tag between two other parties is not ours: (None, None) (the sweep still handles it, as a relay)."""
    mine = my_names() if mine is None else mine
    r = route(subject)
    if not r:
        return None, None
    tag, author, reader, reply = r
    label = ("Re: " + tag) if reply else tag
    if reader in mine:
        return "inbound", label
    if author in mine:
        return "outbound", label
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


_META = ["Subject", "From", "To", "Cc", "Bcc", "Date"]


def _from_reader(f, rec, reader, owner):
    """True if message f is a later message the READER wrote (Hatch and the tower write as the mailbox owner, so only the owner's
    own address counts: a stranger's "Re:" must not retire one of our notes). Tagged: its author is the reader. Untagged: a "Re:"."""
    if email.utils.parseaddr(f["from"])[1].lower() != owner or not f["t"] > rec["t"]:
        return False
    r = route(f["subject"])
    if r:
        return GROUP.get(r[1]) == reader
    return bool(_REPLY_RE.match(f["subject"]))


def _search_meta(q, rec, cache):
    """Metadata of up to 10 messages matching q, other than rec's own message; one search per query per run."""
    if q not in cache:
        found = []
        page = gmail_api._api_retry("GET", "/messages", {"q": q, "maxResults": 10})
        for m in (page.get("messages") or [])[:10]:
            if m.get("id") == rec["message_id"]:
                continue
            msg = gmail_api._api_retry("GET", f"/messages/{m['id']}", {"format": "metadata", "metadataHeaders": _META})
            found.append(_msg_meta(msg) | {"id": m["id"]})
        cache[q] = found
    return cache[q]


def _has_reply(rec, label, reader, cache, owner):
    """A later message about the same topic from the reader: a tagged note the reader wrote, or any "Re:" subject."""
    topic = _topic(rec["subject"], label)
    if not topic:
        return None
    for f in _search_meta(f'subject:"{topic}"', rec, cache):
        if _from_reader(f, rec, reader, owner):
            return f["id"]
    return None


_ABSORBED_WORDS = re.compile(r"\b(trash(?:ed)?|archiv(?:ed|e)|delet(?:ed|e)|absorb(?:ed)?|done|handled|processed|cleared|read and)\b", re.I)
_HOLD_WORDS = re.compile(r"\b(hold(?:s|ing)?|held|keep(?:s|ing)?|kept|waits?|waiting|pending|not yet|on purpose|still open|leave|leaving)\b", re.I)


def _naming_lines(msg_id, did):
    """The text lines of message msg_id that hold the draft ID (full read, plain text parts)."""
    msg = gmail_api._api_retry("GET", f"/messages/{msg_id}", {"format": "full"})
    stack, lines = [msg.get("payload") or {}], []
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts") or [])
        data = (part.get("body") or {}).get("data")
        if data and part.get("mimeType", "").startswith("text/"):
            try:
                text = gmail_api.b64u_decode(data).decode("utf-8", "replace")
            except Exception:
                continue
            lines += [ln for ln in text.splitlines() if did in ln]
    return lines


def _done_note(rec, reader, cache, owner):
    """A later message from the reader that names this draft's ID as done ("trashed r-123"). Naming an ID is not absorption: a line that
    says the reader is holding it, or waiting on something, is not proof (Hatch's 5:52 PM note named two it kept on purpose)."""
    did = rec["draft_id"]
    if not re.fullmatch(r"r-?\d{6,}", did or ""):
        return None
    for f in _search_meta(f'"{did}"', rec, cache):
        if not _from_reader(f, rec, reader, owner):
            continue
        for ln in _naming_lines(f["id"], did):
            if _ABSORBED_WORDS.search(ln) and not _HOLD_WORDS.search(ln):
                return f["id"]
    return None


def proof_of_absorption(rec, reader, label, cache, owner, records):
    """-> text saying how the READER's absorption is proven, or None. Cheapest first; see the sweep section's header."""
    if LABEL_CC_PROCESSED in rec["labels"]:
        return "absorbed (carries Label_32)"
    if {rec["draft_id"], rec["message_id"]} & records.get(reader, set()):
        return f"absorbed (ID in the {reader} published read record)"
    reply = _has_reply(rec, label, reader, cache, owner)
    if reply:
        return f"answered (reply message {reply})"
    done = _done_note(rec, reader, cache, owner)
    if done:
        return f"done-note names it (message {done})"
    return None


def decide(rec, processed, now, cache, owner, mine=None, records=None):
    """-> (action, category, reason). action: delete | keep | other (not a fleet draft; never touched).
    A "keep" whose category ends in "[unabsorbed]" is a note over 24 hours old (ours going out, or another party's) whose reader
    has not been shown to have absorbed it, or an inbound note of ours that we have not handled."""
    mine = my_names() if mine is None else mine
    records = records or {}
    kind, label = classify(rec["subject"], mine)
    age = now - rec["t"]
    ids = {rec["draft_id"], rec["message_id"]}
    handled = bool(ids & processed) or LABEL_CC_PROCESSED in rec["labels"]
    r = route(rec["subject"])
    reader = GROUP.get(r[2]) if r else None
    if kind is None and r:
        kind = "relay"  # between two other parties (Hatch and the tower): NX-01 sweeps it once its reader has absorbed it
        label = ("Re: " if r[3] else "") + r[0]
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
    proof = proof_of_absorption(rec, reader, label, cache, owner, records)
    if proof:
        return "delete", label, f"{proof}, over 24 hours old"
    return "keep", unabsorbed, f"absorption not verified (reader {reader}: no Label_32, no published record entry, no reply, no done-note): kept"


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


def load_records(paths=None):
    """{reader party: set of IDs} from the readers' published read records. A missing file is an empty record (their notes then
    need another proof); it never stops the sweep."""
    out = {}
    for reader, path in (READER_RECORDS if paths is None else paths).items():
        out[reader] = _read_processed(path)
    return out


def write_status(path, now, recs, fleet, records, paths):
    """draft-sweep-status.json: what the hub digest reads. Fleet notes kept without proof, with their pair and time; no bodies."""
    unproven = []
    for r, action, label, reason in fleet:
        if action == "keep" and reason.startswith(("absorption not verified", "not handled yet")):
            p = parties(r["subject"])
            unproven.append({"id": r["draft_id"], "pair": p[0] if p else label, "t": int(r["t"]), "age_h": round((now - r["t"]) / 3600, 1)})
    doc = {"at": int(now), "drafts": len(recs), "fleet": len(fleet), "unproven": unproven,
           "records": {k: {"path": paths.get(k, ""), "ids": len(v)} for k, v in records.items()}}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except OSError:
        pass


def sweep(processed_path, log, apply=False, now=None, sweep_dir=None, out=None, owner=None, mine=None, reader_records=None,
          status_path=None):
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
    paths = READER_RECORDS if reader_records is None else reader_records
    records = load_records(paths)
    labelled = label_absorbed(recs, processed, mine, apply)
    rows = [(r,) + decide(r, processed, now, cache, owner, mine, records) for r in recs]
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
    if apply and status_path:
        write_status(status_path, now, recs, fleet, records, paths)
    print(f"draft sweep ({mode}): {len(recs)} drafts, {len(fleet)} fleet notes, {others} not fleet notes (never touched)", file=out)
    for label in sorted(by_tag):
        print(f"  {label}: {by_tag[label]['delete'] + by_tag[label]['keep']} (delete {by_tag[label]['delete']}, keep {by_tag[label]['keep']})", file=out)
    if fleet:
        oldest = min(fleet, key=lambda row: row[0]["t"])[0]
        print(f"  oldest fleet draft: {oldest['date'] or time.ctime(oldest['t'])}", file=out)
    verb = "deleted" if apply else "would delete"
    print(f"  {verb}: {deleted if apply else len(dels)}", file=out)
    for r, action, label, reason in fleet:
        if action == "delete":
            print(f"  {'deleted' if apply else 'would delete'} {r['draft_id']} {label} {r['subject'][:70]!r}: {reason}", file=out)
    for r, action, label, reason in fleet:
        if action == "keep":
            print(f"  keep {r['draft_id']} {label} {r['subject'][:70]!r}: {reason}", file=out)
    print(f"  not fleet notes remaining: {others}", file=out)
    print(f"  labelled absorbed this pass ({'applied' if apply else 'would label'}): {labelled}", file=out)
    print(f"  unabsorbed older than 24 h (every pair): {unabsorbed}", file=out)
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
    parser.add_argument("--reader-record", action="append", default=None, metavar="PARTY=PATH",
                        help="With --sweep: a reader's published read record (draft IDs, one per line); repeat for several. "
                             "Default: NCC-74656=~/repos/hub/tower/peer-notes-processed.txt")
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
            rr = None
            if args.reader_record:
                rr = dict(READER_RECORDS)
                for item in args.reader_record:
                    party, _, path = item.partition("=")
                    rr[party] = os.path.expanduser(path)
            sweep(args.processed, log, apply=args.apply, reader_records=rr, status_path=STATUS_FILE)
            return 0
        check_notes(args.processed[0], log)
        return 0
    except gmail_api.GmailError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
