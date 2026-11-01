#!/usr/bin/env python3
"""Queue new co-op Talk mentions for the hub without passing chat text to a model.

Private client and self-identification settings stay outside this public backup.
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata


MESSAGE_ID = re.compile(r"^(?:talk|deck):[A-Za-z0-9_-]+:[0-9]+$")
TAG = re.compile(r"@TurtleWolfe|@Jon\b|\bJonathan\b|\bJon's\b", re.IGNORECASE)


def expanded_env_path(name, default):
    return Path(os.path.expanduser(os.environ.get(name, default)))


def safe_text(value):
    text = " ".join(str(value).splitlines())
    text = text.replace("|", "/")
    return "".join(" " if char in "\t\v\f" else char
                   for char in text if unicodedata.category(char) != "Cc")


def message_date(value):
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def date_order(value):
    parsed = message_date(value)
    if parsed is None:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def load_cache(raw_dir):
    messages = []
    for pattern in ("channel-talk-*.json", "channel-deck-*.json"):
        for path in sorted(raw_dir.glob(pattern)):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                print("channel watch: unable to read a cache file", file=sys.stderr)
                continue
            provenance = data.get("_provenance") or {}
            for message in data.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                ident = message.get("id")
                if not isinstance(ident, str) or not MESSAGE_ID.fullmatch(ident):
                    continue
                subject = message.get("subject") or ""
                label = provenance.get("room_name") or subject or path.stem
                sender = message.get("sender") or ""
                body = message.get("plaintextBody") or ""
                if not isinstance(body, str):
                    body = ""
                messages.append({
                    "id": ident,
                    "date": message.get("date") or "",
                    "sender": safe_text(sender),
                    "body": body,
                    "label": safe_text(label),
                    "group": path.name,
                })
    return messages


def sender_local(sender):
    return sender.split("@", 1)[0]


def inbox_line(detail, ident, moment, client):
    return f"- [ ] {moment} | Client talk: {client} | {detail} | {ident}"


def make_lines(messages, since, self_address, client):
    groups = {}
    tags = []
    eligible = []
    for message in messages:
        if message["sender"].casefold() == self_address.casefold() or not message["body"].strip():
            continue
        dated = message_date(message["date"])
        if since is not None and (dated is None or dated.date() < since):
            continue
        eligible.append(message)
        excerpt = safe_text(message["body"])
        if TAG.search(message["body"]):
            detail = f'TAG {sender_local(message["sender"])}, {message["label"]}: "{excerpt[:150]}"'
            tags.append((message["id"], detail))
        else:
            groups.setdefault(message["group"], []).append(message)

    items = [(detail, ident) for ident, detail in tags]
    for grouped in groups.values():
        newest = max(grouped, key=lambda message: date_order(message["date"]))
        senders = list(dict.fromkeys(sender_local(message["sender"]) for message in grouped))
        detail = (f'{grouped[0]["label"]}: {len(grouped)} new ({", ".join(senders)}), newest: '
                  f'"{safe_text(newest["body"])[:80]}"')
        items.append((detail, newest["id"]))
    moment = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [inbox_line(detail, ident, moment, client) for detail, ident in items]
    return lines, tags, eligible, items


def main():
    home = Path.home()
    try:
        machine = (home / ".config/afa/machine").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return 0
    if machine != "NX-01":
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--since", help="dry-run only: include messages dated on or after YYYY-MM-DD")
    args = parser.parse_args()
    if args.since and not args.dry_run:
        parser.error("--since is only available with --dry-run")
    since = None
    if args.since:
        try:
            since = dt.date.fromisoformat(args.since)
        except ValueError:
            parser.error("--since must be YYYY-MM-DD")

    config_path = expanded_env_path("CHANNEL_CONFIG", "~/.config/afa/channel-watch.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("channel watch: unable to read private channel settings", file=sys.stderr)
        return 1
    if not isinstance(config, dict):
        print("channel watch: private channel settings must be a JSON object", file=sys.stderr)
        return 1
    self_address = os.environ.get("CHANNEL_SELF", config.get("self", ""))
    client = os.environ.get("CHANNEL_CLIENT", config.get("client", "co-op chat"))
    if not isinstance(self_address, str) or not self_address:
        print("channel watch: configure CHANNEL_SELF before processing chat", file=sys.stderr)
        return 1
    self_address = self_address.strip()
    if not self_address:
        print("channel watch: configure CHANNEL_SELF before processing chat", file=sys.stderr)
        return 1
    if not isinstance(client, str) or not client:
        print("channel watch: private client label must be a non-empty string", file=sys.stderr)
        return 1
    client = safe_text(client).strip()
    if not client:
        print("channel watch: private client label must be a non-empty string", file=sys.stderr)
        return 1

    raw_dir = expanded_env_path("CHANNEL_RAW", "~/repos/digital-clones/raw")
    puller = expanded_env_path("CHANNEL_PULLER", "~/repos/digital-clones/bin/pull_channels.py")
    state = expanded_env_path("STATE", "~/.claude/state")
    record = expanded_env_path("MUSE_RECORD", str(Path(__file__).with_name("muse_poll_record.sh")))

    try:
        subprocess.run(["python3", str(puller), "--since-days", "3", "--only", "talk,deck"],
                       timeout=180, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print("channel watch: puller timed out after 180 seconds; diffing cache", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError):
        print("channel watch: puller failed; diffing cache", file=sys.stderr)

    messages = load_cache(raw_dir)
    watched_path = state / "client-talk-watched.txt"
    watched_exists = watched_path.exists()
    watched = set()
    if watched_exists:
        try:
            watched = set(watched_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            print("channel watch: unable to read watched state", file=sys.stderr)
            return 1

    if not watched_exists and not (args.dry_run and since is not None):
        if args.dry_run:
            print(f"would seed {len({message['id'] for message in messages})}")
        else:
            state.mkdir(parents=True, exist_ok=True)
            watched_path.write_text("".join(f"{ident}\n" for ident in dict.fromkeys(
                message["id"] for message in messages)), encoding="utf-8")
            print(f"seeded {len({message['id'] for message in messages})}")
        return 0

    candidates = [message for message in messages
                  if (since is not None and args.dry_run and
                      message_date(message["date"]) is not None and
                      message_date(message["date"]).date() >= since)
                  or (message["id"] not in watched and (since is None or not args.dry_run))]
    lines, tags, eligible, items = make_lines(candidates, since, self_address, client)
    if args.dry_run:
        for line in lines:
            print(line)
        return 0

    state.mkdir(parents=True, exist_ok=True)
    for detail, ident in items:
        result = subprocess.run(["bash", str(record), "client-talk", client, detail, ident],
                                env=dict(os.environ, MUSE_STATE=str(state)),
                                capture_output=True, text=True)
        if result.returncode:
            print("channel watch: unable to queue a client-talk line", file=sys.stderr)
            return 1

    if tags:
        wake = state / "client-tag.wake"
        try:
            wake.write_text(f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n" +
                            "".join(f"{ident}\n" for ident, _ in tags), encoding="utf-8")
        except OSError:
            print("channel watch: unable to write the client-tag wake file", file=sys.stderr)
            return 1

    processed_ids = list(dict.fromkeys(message["id"] for message in messages if message["id"] not in watched))
    with watched_path.open("a", encoding="utf-8") as watched_file:
        for ident in processed_ids:
            watched_file.write(f"{ident}\n")
    print(f"client talk count: {len(eligible)} new, {len(tags)} tags")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
