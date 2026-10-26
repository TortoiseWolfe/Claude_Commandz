#!/usr/bin/env python3
"""notes_check.py: check for new notes from Hatch and the second tower.

Read-only toward Gmail: lists drafts and messages matching note queries, drops IDs
already handled or marked Label_32 (agent-notes-cc-processed), and reports new notes.
Never labels, edits, or deletes anything in Gmail. Never prints a secret or token.
"""

import argparse
import email.utils
import os
import sys
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check for new notes from Hatch and the second tower.")
    parser.add_argument("--processed", default=os.path.expanduser("~/.claude/state/agent-notes-processed.txt"),
                        help="Path to processed IDs file")
    parser.add_argument("--log", default=os.path.expanduser("~/.claude/state/notes-check.log"),
                        help="Path to log file")
    args = parser.parse_args(argv)
    log = Logger(args.log)
    try:
        check_notes(args.processed, log)
        return 0
    except gmail_api.GmailError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
